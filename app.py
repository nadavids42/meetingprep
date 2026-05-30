from __future__ import annotations

import re
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from utils.file_parsers import ParsedSource, parse_pasted_notes, parse_uploaded_file
from utils.llm.provider_factory import get_provider
from utils.report_writer import build_markdown_report, safe_report_filename, save_report

load_dotenv()

APP_TITLE = "Meeting Prep Copilot v1"
MAX_SOURCE_CHARS = 24000
NOT_FOUND_VALUES = {
    "",
    "not found",
    "not provided",
    "not found in provided materials",
    "insufficient evidence in source documents",
    "unknown",
    "n/a",
    "none",
}


DEFAULT_METADATA = {
    "client_name": "",
    "meeting_title": "",
    "meeting_date": "",
    "meeting_objective": "",
    "known_attendees": "",
    "specific_concerns": "",
}


def load_prompt(filename: str) -> str:
    return Path("prompts", filename).read_text(encoding="utf-8")


def truncate_for_prompt(text: str, max_chars: int = MAX_SOURCE_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[Source truncated for MVP prompt length control.]"


def normalize_value(value: str | None) -> str:
    if value is None:
        return ""
    value = value.strip().strip("-: ").strip()
    lowered = value.lower().strip(". ")
    if lowered in NOT_FOUND_VALUES:
        return ""
    return value


def build_metadata(
    client_name: str,
    meeting_title: str,
    meeting_date: str,
    meeting_objective: str,
    known_attendees: str,
    specific_concerns: str,
) -> dict[str, str]:
    return {
        "client_name": client_name.strip() or "Not found in provided materials",
        "meeting_title": meeting_title.strip() or "Not found in provided materials",
        "meeting_date": meeting_date.strip() or "Not found in provided materials",
        "meeting_objective": meeting_objective.strip() or "Not found in provided materials",
        "known_attendees": known_attendees.strip() or "Not found in provided materials",
        "specific_concerns": specific_concerns.strip() or "Not found in provided materials",
    }


def extract_context(provider, source: ParsedSource, prompt_template: str) -> str:
    prompt = prompt_template.format(
        source_name=source.name,
        source_type=source.source_type,
        source_text=truncate_for_prompt(source.text),
    )
    return provider.generate(prompt).strip()


def generate_brief(provider, metadata: dict[str, str], extractions: list[tuple[ParsedSource, str]], prompt_template: str) -> str:
    combined_extractions = "\n\n".join(
        f"# Source: {source.name}\nType: {source.source_type}\n\n{extraction}"
        for source, extraction in extractions
    )
    prompt = prompt_template.format(
        combined_extractions=combined_extractions,
        **metadata,
    )
    return provider.generate(prompt).strip()


def collect_sources(uploaded_files, pasted_notes: str) -> tuple[list[ParsedSource], list[str]]:
    sources: list[ParsedSource] = []
    parse_errors: list[str] = []

    for uploaded_file in uploaded_files or []:
        try:
            sources.append(parse_uploaded_file(uploaded_file))
        except Exception as exc:  # noqa: BLE001 - display user-friendly parse errors in app
            parse_errors.append(f"{uploaded_file.name}: {exc}")

    pasted_source = parse_pasted_notes(pasted_notes)
    if pasted_source:
        sources.append(pasted_source)

    return sources, parse_errors


def title_from_filename(name: str) -> str:
    """Create a readable title from a source filename."""
    stem = Path(name).stem
    cleaned = re.sub(r"[_-]+", " ", stem).strip()
    return cleaned.title() if cleaned else ""


def extract_first_heading(text: str) -> str:
    """Return the first Markdown-style or plain-text heading if one is present."""
    for line in text.splitlines():
        cleaned = line.strip().strip("# ").strip()
        if cleaned and len(cleaned) <= 120:
            # Ignore obvious table/spreadsheet dump rows.
            if "," in cleaned and cleaned.count(",") >= 2:
                continue
            return cleaned
    return ""


def find_labeled_value(text: str, labels: list[str]) -> str:
    """Find values such as 'Client: NorthStar Healthcare Staffing' in raw text."""
    label_pattern = "|".join(re.escape(label) for label in labels)
    pattern = rf"(?im)^\s*(?:[-*]\s*)?(?:{label_pattern})\s*:\s*(.+?)\s*$"
    match = re.search(pattern, text)
    if not match:
        return ""
    value = match.group(1).strip()
    # Avoid swallowing a huge block if parsing odd text.
    if len(value) > 160:
        value = value[:160].strip()
    return normalize_value(value)


def find_date_value(text: str) -> str:
    """Find an explicitly labeled date, then fall back to common date patterns."""
    labeled = find_labeled_value(text, ["Date", "Meeting Date", "Target Date"])
    if labeled:
        return labeled

    date_patterns = [
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+\d{4}\b",
        r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
    ]
    for pattern in date_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0)
    return ""


def extract_attendees_block(text: str) -> str:
    """Extract simple attendees lists from notes."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"(?i)^\s*attendees\s*:\s*$", line.strip()):
            attendees: list[str] = []
            for next_line in lines[i + 1 : i + 12]:
                stripped = next_line.strip()
                if not stripped:
                    if attendees:
                        break
                    continue
                if re.match(r"^#{1,6}\s+", stripped) or re.match(r"^[A-Z][A-Za-z ]+\s*$", stripped) and not stripped.startswith("-"):
                    if attendees:
                        break
                if stripped.startswith(("-", "*")):
                    attendees.append(stripped.lstrip("-* ").strip())
                elif attendees:
                    break
            if attendees:
                return "; ".join(attendees)
    return ""


def infer_objective_from_sources(sources: list[ParsedSource]) -> str:
    """Infer a review-screen default objective from repeated project themes."""
    combined = "\n".join(source.text.lower() for source in sources)
    themes: list[str] = []
    if "uat" in combined:
        themes.append("UAT status")
    if "sso" in combined or "security validation" in combined:
        themes.append("SSO/security readiness")
    if "risk" in combined or "blocked" in combined or "at risk" in combined:
        themes.append("open risks and blockers")
    if "go-live" in combined or "go live" in combined:
        themes.append("go-live readiness")
    if "dashboard" in combined or "analytics" in combined or "kpi" in combined:
        themes.append("Analytics/dashboard readiness")

    if not themes:
        return ""
    return "Review " + ", ".join(themes[:5]) + "."


def deterministic_metadata_from_sources(sources: list[ParsedSource]) -> dict[str, str]:
    """Extract obvious metadata directly from raw source text before relying on the LLM.

    This is intentionally conservative, but it catches reliable patterns like
    'Client: NorthStar Healthcare Staffing' even if the LLM extraction omits them.
    """
    detected = DEFAULT_METADATA.copy()

    for source in sources:
        text = source.text

        if not detected["client_name"]:
            detected["client_name"] = find_labeled_value(
                text,
                ["Client", "Client Name", "Customer", "Customer Name", "Account", "Company"],
            )

        if not detected["meeting_title"]:
            explicit_title = find_labeled_value(text, ["Meeting", "Meeting Title", "Session", "Call"])
            if explicit_title:
                detected["meeting_title"] = explicit_title
            else:
                heading = extract_first_heading(text)
                source_title = title_from_filename(source.name)
                candidate = heading or source_title
                if any(keyword in candidate.lower() for keyword in ["steering", "status", "uat", "discovery", "review"]):
                    detected["meeting_title"] = candidate

        if not detected["meeting_date"]:
            detected["meeting_date"] = find_date_value(text)

        if not detected["known_attendees"]:
            detected["known_attendees"] = extract_attendees_block(text)

        if not detected["specific_concerns"]:
            concerns = []
            lower = text.lower()
            if "sso" in lower:
                concerns.append("SSO readiness")
            if "healthcare" in lower and ("attendance" in lower or "adoption" in lower or "participation" in lower):
                concerns.append("Healthcare UAT/adoption")
            if "fill rate" in lower:
                concerns.append("Fill Rate metrics discrepancy")
            if concerns:
                detected["specific_concerns"] = "; ".join(dict.fromkeys(concerns))

    detected["meeting_objective"] = detected["meeting_objective"] or infer_objective_from_sources(sources)

    if any(detected.values()):
        detected["metadata_confidence"] = "Medium"
    else:
        detected["metadata_confidence"] = "Not found in provided materials"

    return detected


def extract_metadata_field(extraction: str, label: str) -> str:
    """Pull a single metadata field out of the extraction Markdown.

    The extraction prompt asks for a Detected Meeting Details section with bullet fields.
    This parser is intentionally simple and forgiving for MVP use.
    """
    patterns = [
        rf"(?im)^\s*[-*]?\s*\*\*{re.escape(label)}\*\*\s*:\s*(.+)$",
        rf"(?im)^\s*[-*]?\s*{re.escape(label)}\s*:\s*(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, extraction)
        if match:
            return normalize_value(match.group(1))
    return ""


def merge_detected_metadata(
    extractions: list[tuple[ParsedSource, str]],
    raw_metadata: dict[str, str] | None = None,
) -> dict[str, str]:
    field_labels = {
        "client_name": "Client Name",
        "meeting_title": "Meeting Title",
        "meeting_date": "Meeting Date",
        "meeting_objective": "Meeting Objective",
        "known_attendees": "Known Attendees",
        "specific_concerns": "Specific Concerns",
    }

    # Start with deterministic metadata from raw parsed source text, then fill blanks
    # from the LLM extraction. This prevents obvious values like "Client: NorthStar"
    # from getting lost if the model is overly cautious.
    detected = DEFAULT_METADATA.copy()
    if raw_metadata:
        for key in DEFAULT_METADATA:
            detected[key] = normalize_value(raw_metadata.get(key, ""))

    confidence_votes: list[str] = []
    if raw_metadata and raw_metadata.get("metadata_confidence"):
        confidence_votes.append(raw_metadata["metadata_confidence"].lower())

    for _, extraction in extractions:
        for key, label in field_labels.items():
            value = extract_metadata_field(extraction, label)
            if value and not detected[key]:
                detected[key] = value

        confidence = extract_metadata_field(extraction, "Metadata Confidence")
        if confidence:
            confidence_votes.append(confidence.lower())

    if "high" in confidence_votes:
        detected["metadata_confidence"] = "High"
    elif "medium" in confidence_votes:
        detected["metadata_confidence"] = "Medium"
    elif "low" in confidence_votes:
        detected["metadata_confidence"] = "Low"
    else:
        detected["metadata_confidence"] = "Not found in provided materials"

    return detected


def reset_app_state() -> None:
    """Clear Streamlit state and force a new file uploader instance."""
    next_uploader_version = st.session_state.get("uploader_version", 0) + 1
    st.session_state.clear()
    st.session_state["uploader_version"] = next_uploader_version
    st.rerun()


def render_source_preview(sources: list[ParsedSource]) -> None:
    if not sources:
        return
    with st.expander("Preview parsed source text"):
        for source in sources:
            st.markdown(f"**{source.name}** · `{source.source_type}` · {len(source.text):,} characters")
            st.text_area(
                label=f"Parsed text: {source.name}",
                value=source.text[:5000],
                height=180,
                disabled=True,
                label_visibility="collapsed",
            )


def render_detected_metadata_form(detected_metadata: dict[str, str]) -> dict[str, str]:
    st.subheader("Review detected meeting details")
    st.caption("These fields were inferred from the uploaded materials. Edit anything before generating the final brief.")

    confidence = detected_metadata.get("metadata_confidence", "Not found in provided materials")
    st.info(f"Detected metadata confidence: {confidence}")

    left, right = st.columns(2)
    with left:
        client_name = st.text_input(
            "Client Name",
            value=detected_metadata.get("client_name", ""),
            key="review_client_name",
        )
        meeting_title = st.text_input(
            "Meeting Title",
            value=detected_metadata.get("meeting_title", ""),
            key="review_meeting_title",
        )
        meeting_date = st.text_input(
            "Meeting Date",
            value=detected_metadata.get("meeting_date", ""),
            key="review_meeting_date",
        )
    with right:
        meeting_objective = st.text_area(
            "Meeting Objective",
            value=detected_metadata.get("meeting_objective", ""),
            height=88,
            key="review_meeting_objective",
        )
        known_attendees = st.text_input(
            "Known Attendees",
            value=detected_metadata.get("known_attendees", ""),
            key="review_known_attendees",
        )
        specific_concerns = st.text_input(
            "Specific Concerns",
            value=detected_metadata.get("specific_concerns", ""),
            key="review_specific_concerns",
        )

    return build_metadata(
        client_name,
        meeting_title,
        meeting_date,
        meeting_objective,
        known_attendees,
        specific_concerns,
    )


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="📝", layout="wide")
    st.title(APP_TITLE)
    st.caption("Turn scattered project context into a concise meeting brief.")

    if "uploader_version" not in st.session_state:
        st.session_state["uploader_version"] = 0

    with st.sidebar:
        st.header("Configuration")

        default_provider = st.session_state.get("llm_provider", "openai")
        provider_choice = st.radio(
            "LLM Provider",
            options=["openai", "anthropic"],
            index=0 if default_provider == "openai" else 1,
            format_func=lambda value: "OpenAI" if value == "openai" else "Anthropic / Claude",
            horizontal=True,
            # help="API keys still come from your .env file. This toggle only selects which configured provider to use.",
        )
        st.session_state["llm_provider"] = provider_choice

        if provider_choice == "openai":
            st.caption("Using OpenAI.")
        else:
            st.caption("Using Anthropic / Claude.")

        if st.button("Clear All", type="secondary", use_container_width=True):
            reset_app_state()
        st.warning("Review AI output before using it with clients.", icon="⚠️")

    st.subheader("Source material")
    st.caption("Upload project notes, emails, trackers, spreadsheets, or paste context below. Meeting details will be detected after extraction.")

    uploaded_files = st.file_uploader(
        "Upload project files",
        type=["txt", "md", "docx", "pdf", "csv", "xlsx"],
        accept_multiple_files=True,
        key=f"uploaded_files_{st.session_state['uploader_version']}",
    )
    pasted_notes = st.text_area("Paste additional context", height=180, key="pasted_notes")

    sources, parse_errors = collect_sources(uploaded_files, pasted_notes)

    for error in parse_errors:
        st.error(error)

    render_source_preview(sources)

    if not sources and "extraction_results" not in st.session_state:
        st.info("Provide at least one source to extract context.")

    if st.button("Extract Context", type="primary", disabled=not bool(sources)):
        try:
            provider = get_provider(st.session_state.get("llm_provider"))
            extract_prompt = load_prompt("extract_context.txt")

            extraction_results: list[tuple[ParsedSource, str]] = []
            progress = st.progress(0, text="Starting extraction...")

            for index, source in enumerate(sources, start=1):
                progress.progress(
                    (index - 1) / max(len(sources), 1),
                    text=f"Extracting context from {source.name}...",
                )
                extraction = extract_context(provider, source, extract_prompt)
                extraction_results.append((source, extraction))

            raw_metadata = deterministic_metadata_from_sources(sources)
            detected_metadata = merge_detected_metadata(extraction_results, raw_metadata)
            st.session_state["extraction_results"] = extraction_results
            st.session_state["detected_metadata"] = detected_metadata
            st.session_state.pop("brief_report", None)
            st.session_state.pop("brief_filename", None)

            progress.progress(1.0, text="Extraction complete. Review detected details below.")
            st.rerun()

        except Exception as exc:  # noqa: BLE001 - Streamlit should show actionable setup/runtime errors
            st.error(f"Could not extract context: {exc}")

    if "extraction_results" in st.session_state and "detected_metadata" in st.session_state:
        st.divider()
        final_metadata = render_detected_metadata_form(st.session_state["detected_metadata"])

        if st.button("Generate Meeting Brief", type="primary"):
            try:
                provider = get_provider(st.session_state.get("llm_provider"))
                brief_prompt = load_prompt("generate_brief.txt")
                extraction_results = st.session_state["extraction_results"]

                with st.spinner("Generating consolidated meeting brief..."):
                    brief = generate_brief(provider, final_metadata, extraction_results, brief_prompt)
                    report = build_markdown_report(final_metadata, brief)
                    filename = safe_report_filename(
                        final_metadata["client_name"],
                        final_metadata["meeting_title"],
                    )
                    save_report(report, filename)

                st.session_state["brief_report"] = report
                st.session_state["brief_filename"] = filename
                st.rerun()

            except Exception as exc:  # noqa: BLE001 - Streamlit should show actionable setup/runtime errors
                st.error(f"Could not generate brief: {exc}")

        with st.expander("Structured extraction results"):
            for source, extraction in st.session_state["extraction_results"]:
                st.markdown(f"### {source.name}")
                st.markdown(extraction)

    if "brief_report" in st.session_state:
        st.divider()
        st.subheader("Final meeting brief")
        st.markdown(st.session_state["brief_report"])

        st.download_button(
            label="Download Markdown Report",
            data=st.session_state["brief_report"],
            file_name=st.session_state.get("brief_filename", "meeting_brief.md"),
            mime="text/markdown",
        )


if __name__ == "__main__":
    main()