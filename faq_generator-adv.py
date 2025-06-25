import streamlit as st
from docx import Document
from docx.shared import Inches
import tempfile
import json
import io
import datetime
import re
import requests
import httpx
from supabase import create_client
import google.generativeai as genai

# --- CONFIG ---
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_API_KEY)

# --- UTILS ---
def load_faqs():
    resp = supabase.table("faqs_adv").select("*").execute()
    return resp.data if resp.data else []

def save_faq_data(faq_id, data):
    supabase.table("faqs_adv").update({"data": data, "updated_at": "now()"}).eq("id", faq_id).execute()

def add_faq(question, assignee):
    data = {"question": question, "assignee": assignee}
    supabase.table("faqs_adv").insert({"data": data}).execute()

def delete_faq(faq_id):
    supabase.table("faqs_adv").delete().eq("id", faq_id).execute()

def upload_screenshot(faq_id, step_num, file):
    file_path = f"{faq_id}/step_{step_num}.png"
    url_upload = f"{SUPABASE_URL}/storage/v1/object/faq-screenshots/{file_path}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "image/png",
        "x-upsert": "true"
    }
    response = httpx.post(url_upload, headers=headers, content=file.getvalue())
    if response.status_code not in [200, 201]:
        st.error(f"Upload failed: {response.status_code}, {response.text}")
        return None
    return f"{SUPABASE_URL}/storage/v1/object/public/faq-screenshots/{file_path}?t={int(datetime.datetime.utcnow().timestamp())}"

def upload_word_doc(faq_id, version, file_content):
    file_path = f"faq-{faq_id}-v{version}.docx"
    url = f"{SUPABASE_URL}/storage/v1/object/faq-docs/{file_path}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "x-upsert": "true"
    }
    response = httpx.post(url, headers=headers, content=file_content.getvalue())
    if response.status_code not in [200, 201]:
        st.error(f"Upload failed: {response.status_code}, {response.text}")
        return None
    return f"{SUPABASE_URL}/storage/v1/object/public/faq-docs/{file_path}"

def parse_uploaded_doc(doc_file):
    doc = Document(doc_file)
    content = {"summary": "", "steps": [], "notes": ""}
    current_section = None

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        lower_text = text.lower()

        if "summary" in lower_text:
            current_section = "summary"
            continue
        if lower_text.startswith("steps") or lower_text.startswith("step"):
            current_section = "step"
            if re.match(r"(step\s*\d+[:\-]?)", lower_text):
                content["steps"].append({"text": text, "query": "", "screenshot": ""})
            continue
        if lower_text.startswith("additional notes") or "note" in lower_text:
            current_section = "notes"
            continue

        if current_section == "summary":
            if lower_text.startswith("steps") or lower_text.startswith("step") or lower_text.startswith("additional notes"):
                current_section = None
            else:
                content["summary"] += " " + text

        elif current_section == "step":
            if re.match(r"(step\s*\d+[:\-]?)", lower_text):
                content["steps"].append({"text": text, "query": "", "screenshot": ""})
            elif "query template" in lower_text or lower_text.startswith("query:"):
                if content["steps"]:
                    content["steps"][-1]["query"] += " " + text
            elif "screenshot for step" in lower_text:
                continue
            elif content["steps"]:
                content["steps"][-1]["text"] += " " + text

        elif current_section == "notes":
            content["notes"] += " " + text

    content["summary"] = content["summary"].strip()
    content["notes"] = content["notes"].strip()
    return content

def validate_with_gemini(question, steps_text):
    model = genai.GenerativeModel("gemini-2.5-flash")
    prompt = f"""The FAQ question is: "{question}".
This question relates to a custom internal FAQ generator app that documents troubleshooting steps.
Here are the user-provided steps:
{steps_text}
Please validate if these steps address the question appropriately, highlight gaps or irrelevant parts, and suggest improvements specific to this app."""
    response = model.generate_content(prompt)
    return response.text.strip()

# --- APP ---
st.title("📄 FAQ Generator + Validator (Advanced)")

st.sidebar.header("➕ Add New FAQ")
new_q = st.sidebar.text_input("New FAQ Question")
new_a = st.sidebar.text_input("Assign to")
if st.sidebar.button("Add FAQ"):
    if new_q and new_a:
        add_faq(new_q, new_a)
        st.sidebar.success("FAQ added! Please refresh.")
    else:
        st.sidebar.warning("Provide both question and assignee.")

faqs = load_faqs()
faq_map = {}
questions = []
assignees_set = set()

for f in faqs:
    data = f.get("data")
    if isinstance(data, dict):
        q = data.get("question")
        a = data.get("assignee")
        if q:
            questions.append(q)
            faq_map[q] = f
        if a:
            assignees_set.add(a)

assignees = list(assignees_set)
assignee = st.selectbox("Select Assignee", assignees) if assignees else None
faq_options = [q for q in questions if faq_map[q]["data"].get("assignee") == assignee] if assignee else []
selected_q = st.selectbox("Select FAQ", faq_options) if faq_options else None
faq_entry = faq_map.get(selected_q)
faq_data = faq_entry["data"] if faq_entry else {}
content = faq_data.get("content", {})

if 'current_faq_id' not in st.session_state:
    st.session_state['current_faq_id'] = None
if 'parsed_doc' not in st.session_state:
    st.session_state['parsed_doc'] = False

if selected_q:
    current_id = faq_entry["id"]
    if st.session_state['current_faq_id'] != current_id:
        st.session_state['steps'] = content.get("steps", [])
        st.session_state['parsed_doc'] = False
        st.session_state['summary'] = content.get("summary", "")
        st.session_state['notes'] = content.get("notes", "")
        for k in list(st.session_state.keys()):
            if k.startswith("step_text_") or k.startswith("step_query_") or k.startswith("step_ss_"):
                st.session_state.pop(k)
        st.session_state['current_faq_id'] = current_id

uploaded_doc = st.file_uploader("Upload Existing FAQ Word Document (Optional)", type="docx")
if uploaded_doc and not st.session_state['parsed_doc']:
    content = parse_uploaded_doc(uploaded_doc)
    st.session_state['steps'] = content.get("steps", [])
    st.session_state['parsed_summary'] = content.get("summary", "").strip()
    st.session_state['parsed_notes'] = content.get("notes", "").strip()
    st.session_state['parsed_doc'] = True
    st.success("Document parsed! Review below.")
    with st.expander("🔍 Parsed Document JSON (click to expand)"):
        st.json(content)

if 'parsed_summary' in st.session_state:
    with st.expander("🔍 Parsed Document Summary"):
        st.write(st.session_state['parsed_summary'])
    if st.button("Replace form summary with parsed summary"):
        st.session_state['summary'] = st.session_state['parsed_summary']

if 'parsed_notes' in st.session_state:
    with st.expander("🔍 Parsed Document Notes"):
        st.write(st.session_state['parsed_notes'])
    if st.button("Replace form notes with parsed notes"):
        st.session_state['notes'] = st.session_state['parsed_notes']

summary = st.text_area("Summary", key="summary")
notes = st.text_area("Notes", key="notes")

if 'steps' not in st.session_state:
    st.session_state['steps'] = content.get("steps", [])

if st.button("Add Step"):
    st.session_state['steps'].append({"text": "", "query": "", "screenshot": ""})

for idx, step in enumerate(st.session_state['steps']):
    if f"step_text_{idx}" not in st.session_state:
        st.session_state[f"step_text_{idx}"] = step["text"]
    if f"step_query_{idx}" not in st.session_state:
        st.session_state[f"step_query_{idx}"] = step["query"]

    st.session_state['steps'][idx]["text"] = st.text_input(f"Step {idx+1} Text", key=f"step_text_{idx}")
    st.session_state['steps'][idx]["query"] = st.text_area(f"Step {idx+1} Query", key=f"step_query_{idx}")

    uploaded_ss = st.file_uploader(
        f"Upload / Paste Screenshot for Step {idx+1}",
        type=["png", "jpg", "jpeg"],
        help="Drag, drop or paste screenshot.",
        key=f"step_ss_{idx}"
    )
    if uploaded_ss and faq_entry:
        url = upload_screenshot(faq_entry["id"], idx+1, uploaded_ss)
        if url:
            st.session_state['steps'][idx]["screenshot"] = url
    if step["screenshot"]:
        st.image(step["screenshot"], caption=f"Step {idx+1} Screenshot")

if st.button("Validate with Gemini") and selected_q:
    steps_text = "\n".join([f"Step {i+1}: {s['text']}" for i, s in enumerate(st.session_state['steps'])])
    with st.spinner("Validating..."):
        feedback = validate_with_gemini(selected_q, steps_text)
    st.subheader("Gemini Feedback")
    st.write(feedback)
