from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import streamlit as st

PLAN_FILE = Path(__file__).with_name("plan.json")
LOG_FILE = Path(__file__).with_name("training_log.json")
PASSWORD_FILE = Path(__file__).with_name("passwort.txt")

WEEKDAY_DE = {
    "monday": "Montag",
    "tuesday": "Dienstag",
    "wednesday": "Mittwoch",
    "thursday": "Donnerstag",
    "friday": "Freitag",
    "saturday": "Samstag",
    "sunday": "Sonntag",
}


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def resolve_day_plan(plan: dict[str, Any], weekday_key: str) -> dict[str, Any]:
    week_plan = plan.get("week_plan", {})
    day_item = week_plan.get(weekday_key, {})

    if "same_as" in day_item:
        same_day = day_item["same_as"]
        return week_plan.get(same_day, {})

    return day_item


def format_stat(value: float | int | None, decimals: int = 1) -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return str(value)
    return f"{value:.{decimals}f}"


def metric_stats(entries: list[dict[str, Any]], exercise_name: str, metric_key: str) -> tuple[float | int | None, float | int | None]:
    values: list[float | int] = []
    for entry in entries:
        if entry.get("exercise") == exercise_name and metric_key in entry:
            values.append(entry[metric_key])

    if not values:
        return None, None

    last_value = values[-1]
    average = sum(values) / len(values)

    # Reps are best shown as whole number stats.
    if metric_key == "reps":
        return int(round(average)), int(last_value)

    return float(average), float(last_value)


def metric_today_value(
    entries: list[dict[str, Any]],
    day_iso: str,
    section_name: str,
    exercise_name: str,
    metric_key: str,
) -> float | int | bool | str | None:
    value: float | int | bool | str | None = None
    for entry in entries:
        if entry.get("date") != day_iso:
            continue
        if entry.get("section") != section_name:
            continue
        if entry.get("exercise") != exercise_name:
            continue
        if metric_key in entry:
            value = entry[metric_key]
    return value


def upsert_day_metric(
    entries: list[dict[str, Any]],
    *,
    timestamp: str,
    day_iso: str,
    weekday_key: str,
    section: str,
    exercise: str,
    metric_key: str,
    value: float | int | bool | str,
) -> bool:
    # Update existing entry for today/exercise/metric, otherwise append a new one.
    for idx in range(len(entries) - 1, -1, -1):
        entry = entries[idx]
        if entry.get("date") != day_iso:
            continue
        if entry.get("section") != section:
            continue
        if entry.get("exercise") != exercise:
            continue
        if metric_key not in entry:
            continue

        entry["timestamp"] = timestamp
        entry["weekday"] = weekday_key
        entry["section"] = section
        entry[metric_key] = value
        entries[idx] = entry
        return True

    entries.append(
        {
            "timestamp": timestamp,
            "date": day_iso,
            "weekday": weekday_key,
            "section": section,
            "exercise": exercise,
            metric_key: value,
        }
    )
    return False


def safe_key(section_name: str, exercise_name: str, metric_name: str) -> str:
    raw = f"{section_name}_{exercise_name}_{metric_name}".lower()
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in raw)
    return "_".join(part for part in cleaned.split("_") if part)


def variant_label(variant: dict[str, Any]) -> str:
    custom_label = variant.get("label")
    if custom_label:
        return str(custom_label)

    sets = variant.get("sets", "?")
    target = variant.get("target", "?")
    mode = variant.get("id", "variante")
    return f"{sets} x {target} ({mode})"


def get_saved_variant_id(
    entries: list[dict[str, Any]],
    day_iso: str,
    section_name: str,
    exercise_name: str,
    variants: list[dict[str, Any]],
) -> str | None:
    today_variant = metric_today_value(entries, day_iso, section_name, exercise_name, "variant")
    valid_ids = [variant.get("id", f"var_{idx}") for idx, variant in enumerate(variants)]
    if isinstance(today_variant, str) and today_variant in valid_ids:
        return today_variant
    return valid_ids[0] if valid_ids else None


def get_selected_variant(
    entries: list[dict[str, Any]],
    day_iso: str,
    section_name: str,
    exercise: dict[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    exercise_name = exercise.get("name", "Unbenannte Übung")
    variants = exercise.get("variants", [])
    if not variants:
        return None, exercise

    variant_key = safe_key(section_name, exercise_name, "variant")
    saved_variant_id = get_saved_variant_id(entries, day_iso, section_name, exercise_name, variants)
    selected_variant_id = st.session_state.get(variant_key, saved_variant_id)

    for idx, variant in enumerate(variants):
        variant_id = variant.get("id", f"var_{idx}")
        if variant_id == selected_variant_id:
            return selected_variant_id, variant

    return saved_variant_id, variants[0]


def get_saved_section_mode(
    entries: list[dict[str, Any]],
    day_iso: str,
    section_name: str,
    mode_selector: dict[str, Any],
) -> str | None:
    selector_id = str(mode_selector.get("id", "mode"))
    metric_key = f"mode_{selector_id}"
    saved_value = metric_today_value(entries, day_iso, section_name, "__section__", metric_key)
    options = [str(option.get("id")) for option in mode_selector.get("options", [])]
    default_value = str(mode_selector.get("default", options[0] if options else ""))

    if isinstance(saved_value, str) and saved_value in options:
        return saved_value
    if default_value in options:
        return default_value
    return options[0] if options else None


def resolve_section_exercises(
    section: dict[str, Any],
    entries: list[dict[str, Any]],
    day_iso: str,
    section_name: str,
    use_session: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str | None]:
    mode_selector = section.get("mode_selector")
    if not mode_selector:
        return section.get("exercises", []), None, None

    selected_mode = get_saved_section_mode(entries, day_iso, section_name, mode_selector)
    selector_key = safe_key(section_name, "section", str(mode_selector.get("id", "mode")))
    options = [str(option.get("id")) for option in mode_selector.get("options", [])]
    if use_session and selector_key in st.session_state and st.session_state[selector_key] in options:
        selected_mode = st.session_state[selector_key]

    exercises = section.get("exercises_by_mode", {}).get(selected_mode, [])
    return exercises, mode_selector, selected_mode


def is_completed_value(value: float | int | bool | str | None) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (float, int)):
        return value > 0
    if isinstance(value, str):
        return bool(value.strip())
    return False


def current_metric_value(
    entries: list[dict[str, Any]],
    day_iso: str,
    section_name: str,
    exercise_name: str,
    metric_key: str,
) -> float | int | bool | str | None:
    widget_key = safe_key(section_name, exercise_name, metric_key)
    if widget_key in st.session_state:
        return st.session_state[widget_key]
    return metric_today_value(entries, day_iso, section_name, exercise_name, metric_key)


def exercise_is_complete(
    entries: list[dict[str, Any]],
    day_iso: str,
    section_name: str,
    exercise: dict[str, Any],
) -> bool:
    exercise_name = exercise.get("name", "Unbenannte Übung")
    _, selected_variant = get_selected_variant(entries, day_iso, section_name, exercise)
    track = selected_variant.get("track", {})

    metric_names = [metric_name for metric_name in ("weight", "reps", "duration_min") if track.get(metric_name)]
    if metric_names:
        return all(
            is_completed_value(current_metric_value(entries, day_iso, section_name, exercise_name, metric_name))
            for metric_name in metric_names
        )

    return is_completed_value(current_metric_value(entries, day_iso, section_name, exercise_name, "completed"))


def section_status_label(
    entries: list[dict[str, Any]],
    day_iso: str,
    section_name: str,
    exercises: list[dict[str, Any]],
) -> str:
    total_exercises = len(exercises)
    completed_exercises = sum(1 for exercise in exercises if exercise_is_complete(entries, day_iso, section_name, exercise))

    if total_exercises == 0 or completed_exercises == 0:
        return f"{section_name} - offen"
    if completed_exercises == total_exercises:
        return f"{section_name} - fertig"
    return f"{section_name} - {completed_exercises}/{total_exercises} geschafft"


def saved_metric_value(
    entries: list[dict[str, Any]],
    day_iso: str,
    section_name: str,
    exercise_name: str,
    metric_key: str,
) -> float | int | bool | str | None:
    return metric_today_value(entries, day_iso, section_name, exercise_name, metric_key)


def selected_variant_for_day(
    entries: list[dict[str, Any]],
    day_iso: str,
    section_name: str,
    exercise: dict[str, Any],
) -> dict[str, Any]:
    variants = exercise.get("variants", [])
    if not variants:
        return exercise

    saved_variant_id = get_saved_variant_id(entries, day_iso, section_name, exercise.get("name", "Unbenannte Übung"), variants)
    for idx, variant in enumerate(variants):
        variant_id = variant.get("id", f"var_{idx}")
        if variant_id == saved_variant_id:
            return variant

    return variants[0]


def exercise_is_complete_for_day(
    entries: list[dict[str, Any]],
    day_iso: str,
    section_name: str,
    exercise: dict[str, Any],
) -> bool:
    exercise_name = exercise.get("name", "Unbenannte Übung")
    selected_variant = selected_variant_for_day(entries, day_iso, section_name, exercise)
    track = selected_variant.get("track", {})
    metric_names = [metric_name for metric_name in ("weight", "reps", "duration_min") if track.get(metric_name)]

    if metric_names:
        return all(
            is_completed_value(saved_metric_value(entries, day_iso, section_name, exercise_name, metric_name))
            for metric_name in metric_names
        )

    return is_completed_value(saved_metric_value(entries, day_iso, section_name, exercise_name, "completed"))


def day_completion_counts(plan: dict[str, Any], entries: list[dict[str, Any]], target_date: date) -> tuple[int, int]:
    weekday_key = target_date.strftime("%A").lower()
    day_plan = resolve_day_plan(plan, weekday_key)
    sections = day_plan.get("sections", [])
    day_iso = target_date.isoformat()

    total_exercises = 0
    completed_exercises = 0

    for section in sections:
        section_name = section.get("name", "Unbenannter Abschnitt")
        exercises, _, _ = resolve_section_exercises(section, entries, day_iso, section_name, use_session=False)
        total_exercises += len(exercises)
        completed_exercises += sum(
            1 for exercise in exercises if exercise_is_complete_for_day(entries, day_iso, section_name, exercise)
        )

    return completed_exercises, total_exercises


def day_progress_color(completed_exercises: int, total_exercises: int) -> str:
    if total_exercises == 0 or completed_exercises == 0:
        return "rgba(148, 163, 184, 0.35)"

    ratio = completed_exercises / total_exercises
    if ratio >= 1:
        return "rgba(34, 197, 94, 0.95)"

    alpha = 0.2 + (0.65 * ratio)
    return f"rgba(234, 179, 8, {alpha:.2f})"


def render_last_30_days(plan: dict[str, Any], entries: list[dict[str, Any]], today: date) -> None:
    boxes: list[str] = []

    for offset in range(29, -1, -1):
        target_date = today - timedelta(days=offset)
        completed_exercises, total_exercises = day_completion_counts(plan, entries, target_date)
        color = day_progress_color(completed_exercises, total_exercises)
        if total_exercises == 0:
            label = f"{target_date.isoformat()}: kein Plan"
        elif completed_exercises == 0:
            label = f"{target_date.isoformat()}: 0/{total_exercises} geschafft"
        elif completed_exercises == total_exercises:
            label = f"{target_date.isoformat()}: alles geschafft"
        else:
            label = f"{target_date.isoformat()}: {completed_exercises}/{total_exercises} geschafft"

        boxes.append(
            "".join(
                [
                    f'<div title="{label}" style="',
                    "width:16px; height:16px; border-radius:4px; margin:2px; ",
                    f"background:{color}; border:1px solid rgba(15, 23, 42, 0.10);",
                    '"></div>',
                ]
            )
        )

    st.markdown(
        "".join(
            [
                '<div style="margin: 0.35rem 0 1rem 0;">',
                '<div style="font-size:0.9rem; color:#475569; margin-bottom:0.35rem;">Letzte 30 Tage</div>',
                '<div style="display:flex; flex-wrap:wrap; gap:0; align-items:center;">',
                *boxes,
                "</div>",
                '<div style="font-size:0.8rem; color:#64748b; margin-top:0.35rem;">Grau: nichts erledigt, Gelb: teilweise, Gruen: alles geschafft</div>',
                "</div>",
            ]
        ),
        unsafe_allow_html=True,
    )


def persist_training_items(
    log_data: dict[str, Any],
    entries: list[dict[str, Any]],
    metric_items: list[dict[str, Any]],
    completion_items: list[dict[str, Any]],
    *,
    day_iso: str,
    weekday_key: str,
) -> tuple[int, int]:
    timestamp = datetime.now().isoformat(timespec="seconds")
    added_count = 0
    updated_count = 0

    for metric_item in metric_items:
        value = metric_item["value"]
        if isinstance(value, (float, int)) and value <= 0:
            continue

        was_updated = upsert_day_metric(
            entries,
            timestamp=timestamp,
            day_iso=day_iso,
            weekday_key=weekday_key,
            section=metric_item["section"],
            exercise=metric_item["exercise"],
            metric_key=metric_item["metric"],
            value=value,
        )
        if was_updated:
            updated_count += 1
        else:
            added_count += 1

    for completion_item in completion_items:
        existing_completed = metric_today_value(
            entries,
            day_iso,
            completion_item["section"],
            completion_item["exercise"],
            "completed",
        )
        if not completion_item["value"] and existing_completed is None:
            continue

        was_updated = upsert_day_metric(
            entries,
            timestamp=timestamp,
            day_iso=day_iso,
            weekday_key=weekday_key,
            section=completion_item["section"],
            exercise=completion_item["exercise"],
            metric_key="completed",
            value=completion_item["value"],
        )
        if was_updated:
            updated_count += 1
        else:
            added_count += 1

    log_data["entries"] = entries
    save_json(LOG_FILE, log_data)
    return added_count, updated_count


def load_password() -> str | None:
    if not PASSWORD_FILE.exists():
        return None
    return PASSWORD_FILE.read_text(encoding="utf-8").strip()


def show_password_gate() -> bool:
    expected_password = load_password()
    if not expected_password:
        st.error("Passwortdatei fehlt oder ist leer. Bitte passwort.txt prüfen.")
        return False

    if st.session_state.get("authenticated", False):
        return True

    st.title("FitMarv Login")
    st.caption("Bitte Passwort eingeben, um den Trainingsplan zu sehen.")

    with st.form("login_form"):
        entered_password = st.text_input("Passwort", type="password")
        submitted = st.form_submit_button("Anmelden")

    if submitted:
        if entered_password == expected_password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Falsches Passwort.")

    return False


def main() -> None:
    st.set_page_config(page_title="FitMarv Trainingsplan", layout="centered")

    if not show_password_gate():
        return

    plan = load_json(PLAN_FILE, default={})
    log_data = load_json(LOG_FILE, default={"entries": []})
    entries = log_data.get("entries", [])

    today = date.today()
    weekday_key = today.strftime("%A").lower()
    weekday_label = WEEKDAY_DE.get(weekday_key, weekday_key.capitalize())

    day_plan = resolve_day_plan(plan, weekday_key)
    sections = day_plan.get("sections", [])
    day_iso = today.isoformat()

    st.title("FitMarv Tages-Trainingsplan")
    render_last_30_days(plan, entries, today)
    st.caption(f"Heute: {weekday_label} ({today.isoformat()})")

    flash_message = st.session_state.pop("flash_message", None)
    if flash_message:
        st.success(flash_message)

    if not sections:
        st.error("Kein Trainingsplan fuer heute gefunden. Bitte plan.json prüfen.")
        return

    metrics_to_log: list[dict[str, Any]] = []
    completion_to_log: list[dict[str, Any]] = []

    for section in sections:
        section_name = section.get("name", "Unbenannter Abschnitt")
        section_type = section.get("type", "other")
        exercises_for_status, _, _ = resolve_section_exercises(section, entries, day_iso, section_name, use_session=True)

        section_open_key = safe_key(section_name, "section", "open")
        if section_open_key not in st.session_state:
            st.session_state[section_open_key] = False

        header_col, toggle_col = st.columns([0.8, 0.2])
        with header_col:
            st.markdown(f"### {section_status_label(entries, day_iso, section_name, exercises_for_status)}")
        with toggle_col:
            toggle_label = "Zuklappen" if st.session_state[section_open_key] else "Aufklappen"
            if st.button(toggle_label, key=safe_key(section_name, "section", "toggle")):
                st.session_state[section_open_key] = not st.session_state[section_open_key]
                st.rerun()

        if not st.session_state[section_open_key]:
            st.divider()
            continue

        exercises, mode_selector, selected_mode = resolve_section_exercises(section, entries, day_iso, section_name, use_session=True)
        if mode_selector and selected_mode is not None:
            options = [str(option.get("id")) for option in mode_selector.get("options", [])]
            selector_key = safe_key(section_name, "section", str(mode_selector.get("id", "mode")))
            selected_mode = st.selectbox(
                str(mode_selector.get("label", "Modus")),
                options=options,
                index=options.index(selected_mode) if selected_mode in options else 0,
                format_func=lambda value: str(next((o.get("label", o.get("id", value)) for o in mode_selector.get("options", []) if str(o.get("id")) == value), value)),
                key=selector_key,
            )
            exercises = section.get("exercises_by_mode", {}).get(selected_mode, [])
            metrics_to_log.append(
                {
                    "section": section_name,
                    "exercise": "__section__",
                    "metric": f"mode_{mode_selector.get('id', 'mode')}",
                    "value": selected_mode,
                }
            )

        if section_type == "stretch":
            for exercise in exercises:
                exercise_name = exercise.get("name", "Unbenannt")
                sets = exercise.get("sets", 0)
                target = exercise.get("target", "?")
                today_done = metric_today_value(entries, day_iso, section_name, exercise_name, "completed")
                done_value = st.checkbox(
                    f"{exercise_name}: {sets} x {target}",
                    value=bool(today_done),
                    key=safe_key(section_name, exercise_name, "completed"),
                )
                completion_to_log.append(
                    {
                        "section": section_name,
                        "exercise": exercise_name,
                        "metric": "completed",
                        "value": bool(done_value),
                    }
                )
        else:
            for exercise in exercises:
                exercise_name = exercise.get("name", "Unbenannte Übung")
                st.markdown(f"**{exercise_name}**")
                
                variants = exercise.get("variants", [])
                selected_variant_id, selected_variant = get_selected_variant(entries, day_iso, section_name, exercise)

                if variants:
                    options = [variant.get("id", f"var_{idx}") for idx, variant in enumerate(variants)]
                    labels = {variant.get("id", f"var_{idx}"): variant_label(variant) for idx, variant in enumerate(variants)}
                    selected_variant_id = st.selectbox(
                        "Variante",
                        options=options,
                        index=options.index(selected_variant_id) if selected_variant_id in options else 0,
                        format_func=lambda opt: labels.get(opt, opt),
                        key=safe_key(section_name, exercise_name, "variant"),
                    )
                    selected_variant = next((item for item in variants if item.get("id") == selected_variant_id), variants[0])

                sets = selected_variant.get("sets", "?")
                target = selected_variant.get("target", "?")
                track = selected_variant.get("track", {})

                if selected_variant_id is not None:
                    st.caption(f"Aktive Variante: {variant_label(selected_variant)}")

                choice = selected_variant.get("choice")
                if choice and choice.get("options"):
                    choice_options = [str(option.get("id")) for option in choice.get("options", [])]
                    choice_default = metric_today_value(entries, day_iso, section_name, exercise_name, "choice")
                    if not isinstance(choice_default, str) or choice_default not in choice_options:
                        choice_default = str(choice.get("default", choice_options[0] if choice_options else ""))
                    selected_choice = st.selectbox(
                        str(choice.get("label", "Auswahl")),
                        options=choice_options,
                        index=choice_options.index(choice_default) if choice_default in choice_options else 0,
                        format_func=lambda value: str(next((o.get("label", o.get("id", value)) for o in choice.get("options", []) if str(o.get("id")) == value), value)),
                        key=safe_key(section_name, exercise_name, "choice"),
                    )
                    metrics_to_log.append(
                        {
                            "section": section_name,
                            "exercise": exercise_name,
                            "metric": "choice",
                            "value": selected_choice,
                        }
                    )

                st.write(f"Ziel: {sets} x {target}")

                if selected_variant.get("description"):
                    st.caption(str(selected_variant.get("description")))

                if selected_variant_id is not None:
                    variant_metric = {
                        "section": section_name,
                        "exercise": exercise_name,
                        "metric": "variant",
                        "value": selected_variant_id,
                    }
                    metrics_to_log.append(variant_metric)

                has_input = bool(track.get("weight") or track.get("reps") or track.get("duration_min"))
                if not has_input:
                    today_done = metric_today_value(entries, day_iso, section_name, exercise_name, "completed")
                    done_value = st.checkbox(
                        "Erledigt",
                        value=bool(today_done),
                        key=safe_key(section_name, exercise_name, "completed"),
                    )
                    completion_metric = {
                        "section": section_name,
                        "exercise": exercise_name,
                        "metric": "completed",
                        "value": bool(done_value),
                    }
                    completion_to_log.append(completion_metric)

                if track.get("weight"):
                    avg_weight, last_weight = metric_stats(entries, exercise_name, "weight")
                    today_weight = metric_today_value(entries, day_iso, section_name, exercise_name, "weight")
                    weight_label = (
                        "Gewicht (kg) "
                        f"| Durchschnitt: {format_stat(avg_weight)} "
                        f"| Letzter Wert: {format_stat(last_weight)}"
                    )
                    weight_value = st.number_input(
                        weight_label,
                        min_value=0.0,
                        step=0.5,
                        value=float(today_weight) if today_weight is not None else 0.0,
                        key=safe_key(section_name, exercise_name, "weight"),
                    )
                    if today_weight is not None and today_weight > 0:
                        st.caption("Heute bereits erledigt, Änderungen überschreiben aktuellen Wert")
                    weight_metric = {
                        "section": section_name,
                        "exercise": exercise_name,
                        "metric": "weight",
                        "value": weight_value,
                    }
                    metrics_to_log.append(weight_metric)

                if track.get("reps"):
                    avg_reps, last_reps = metric_stats(entries, exercise_name, "reps")
                    today_reps = metric_today_value(entries, day_iso, section_name, exercise_name, "reps")
                    reps_label = (
                        "Wiederholungen "
                        f"| Durchschnitt: {format_stat(avg_reps, 0)} "
                        f"| Letzter Wert: {format_stat(last_reps, 0)}"
                    )
                    reps_value = st.number_input(
                        reps_label,
                        min_value=0,
                        step=1,
                        value=int(today_reps) if today_reps is not None else 0,
                        key=safe_key(section_name, exercise_name, "reps"),
                    )
                    if today_reps is not None and today_reps > 0:
                        st.caption("Heute bereits erledigt, Änderungen überschreiben aktuellen Wert")
                    reps_metric = {
                        "section": section_name,
                        "exercise": exercise_name,
                        "metric": "reps",
                        "value": int(reps_value),
                    }
                    metrics_to_log.append(reps_metric)

                if track.get("duration_min"):
                    avg_min, last_min = metric_stats(entries, exercise_name, "duration_min")
                    today_min = metric_today_value(entries, day_iso, section_name, exercise_name, "duration_min")
                    cardio_label = (
                        "Dauer (min) "
                        f"| Durchschnitt: {format_stat(avg_min)} "
                        f"| Letzter Wert: {format_stat(last_min)}"
                    )
                    duration_value = st.number_input(
                        cardio_label,
                        min_value=0.0,
                        step=1.0,
                        value=float(today_min) if today_min is not None else 0.0,
                        key=safe_key(section_name, exercise_name, "duration_min"),
                    )
                    if today_min is not None and today_min > 0:
                        st.caption("Heute bereits erledigt, Änderungen überschreiben aktuellen Wert")
                    duration_metric = {
                        "section": section_name,
                        "exercise": exercise_name,
                        "metric": "duration_min",
                        "value": duration_value,
                    }
                    metrics_to_log.append(duration_metric)

        st.divider()

    submitted = st.button("Training speichern")

    if submitted:
        added_count, updated_count = persist_training_items(
            log_data,
            entries,
            metrics_to_log,
            completion_to_log,
            day_iso=day_iso,
            weekday_key=weekday_key,
        )

        if added_count == 0 and updated_count == 0:
            st.warning("Keine Werte gespeichert. Trage für mindestens ein Feld einen Wert größer 0 ein.")
        else:
            st.success(f"Gespeichert: {added_count} neu, {updated_count} aktualisiert")


if __name__ == "__main__":
    main()
