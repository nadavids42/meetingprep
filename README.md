# Meeting Prep Copilot v1

Meeting Prep Copilot turns scattered project context into a concise, meeting-ready brief.

## What it does

1. Upload project files or paste notes.
2. Extract context from each source independently.
3. Detect likely meeting metadata from the materials.
4. Let the user review/edit detected meeting details.
5. Generate a final Markdown meeting brief.
6. Download the report.

## Supported files

- `.txt`
- `.md`
- `.docx`
- `.pdf`
- `.csv`
- `.xlsx`

Spreadsheet support is intentionally lightweight. It is meant to surface meeting context from trackers, not perform full analytics.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Mac/Linux
# .venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

Copy the example environment file:

```bash
cp .env.example .env
```

Then set your provider and API key.

### OpenAI

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your_key_here
```

### Anthropic

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_key_here
```

## Run

```bash
streamlit run app.py
```

Then open the local Streamlit URL shown in your terminal.

## Workflow

The app uses a two-step UI:

### 1. Extract Context

The app parses all sources and sends each source through the extraction prompt. Each source returns structured context plus detected meeting details.

### 2. Review Detected Meeting Details

After extraction, the app shows editable metadata fields:

- Client Name
- Meeting Title
- Meeting Date
- Meeting Objective
- Known Attendees
- Specific Concerns

This keeps the app lightweight while reducing the amount of manual form entry up front.

### 3. Generate Meeting Brief

The app sends the reviewed metadata plus all source extractions through the synthesis prompt and generates the final brief.

## Clear All

Use the **Clear All** button in the sidebar to reset the app state, remove uploaded files, clear pasted notes, and start a new demo.

## Provider abstraction

The LLM provider is selected through environment variables. The main app talks to a common provider interface, so the workflow does not depend on one specific model vendor.

Current providers:

- OpenAI
- Anthropic / Claude

## Project structure

```text
meeting-prep-copilot/
├── app.py
├── requirements.txt
├── .env.example
├── README.md
├── prompts/
│   ├── extract_context.txt
│   └── generate_brief.txt
├── sample_docs/
├── outputs/
└── utils/
    ├── file_parsers.py
    ├── report_writer.py
    └── llm/
        ├── base_provider.py
        ├── openai_provider.py
        ├── anthropic_provider.py
        └── provider_factory.py
```

## Demo positioning

This project is designed around a real consulting problem: useful meeting context is often spread across notes, emails, status updates, trackers, and spreadsheets. The app reduces cognitive load by turning that scattered material into a structured prep brief.
