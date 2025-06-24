import streamlit as st
from docx import Document
from docx.shared import Inches
import tempfile
import json
import io
import datetime
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
    supabase.storage.from_("faq-screenshots").upload(file_path, file, {"content-type": "image/png", "upsert": True})
    return f"{SUPABASE_URL}/storage/v1/object/public/faq-screenshots/{file_path}"

def upload_word_doc(faq_id, version, file_content):
    file_path = f"faq-{faq_id}-v{version}.docx"
    supabase.storage.from_("faq-docs").upload(file_path, file_content, {
        "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "upsert": True
    })
    return f"{SUPABASE_URL}/storage/v1/object/public/faq-docs/{file_path}"

def parse_uploaded_doc(doc_file):
    doc = Document(doc_file)
    content = {"summary": "", "steps": [], "notes": ""}
    current_section = ""
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if text.lower().startswith("summary"):
            current_section = "summary"
            continue
        if text.lower().startswith("step"):
            current_section = "step"
            content["steps"].append({"text": text, "query": "", "screenshot": ""})
            continue
        if text.lower().startswith("notes"):
            current_section = "notes"
            continue
        if current_section == "summary":
            content["summary"] += " " + text
        elif current_section == "step":
            content["steps"][-1]["text"] += " " + text
        elif current_section == "notes":
            content["notes"] += " " + text
    return content

def validate_with_gemini(question, steps_text):
    model = genai.GenerativeModel("gemini-2.5-flash")
    prompt = f"""The FAQ question is: "{question}".
Here are the step-by-step instructions:
{steps_text}
Validate if these steps address the FAQ question, highlight gaps, suggest improvements."""
    response = model.generate_content(prompt)
    return response.text.strip()

# --- APP START ---
st.title("📄 FAQ Generator + Validator (Advanced)")

faqs = load_faqs()

# Build questions, map, assignees safely
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

# --- Add New FAQ ---
st.sidebar.header("➕ Add New FAQ")
new_q = st.sidebar.text_input("New FAQ Question")
new_a = st.sidebar.text_input("Assign to")
if st.sidebar.button("Add FAQ"):
    if new_q and new_a:
        add_faq(new_q, new_a)
        st.sidebar.success("FAQ added! Refresh to see it.")
    else:
        st.sidebar.warning("Please provide both question + assignee.")

# --- Select Assignee + FAQ ---
assignee = st.selectbox("Select Assignee", assignees) if assignees else None
faq_options = [
    q for q in questions
    if faq_map[q]["data"].get("assignee") == assignee
] if assignee else []

selected_q = st.selectbox("Select FAQ", faq_options) if faq_options else None

faq_entry = faq_map.get(selected_q)
faq_data = faq_entry["data"] if faq_entry else {}
content = faq_data.get("content", {})

# --- Delete FAQ ---
if selected_q and st.button("🗑️ Delete this FAQ"):
    delete_faq(faq_entry["id"])
    st.success("FAQ deleted. Please refresh.")
    st.stop()

# --- Upload Word Doc Anytime ---
if selected_q:
    uploaded_doc = st.file_uploader("Upload Existing FAQ Word Document (Optional)", type="docx")
    if uploaded_doc:
        content = parse_uploaded_doc(uploaded_doc)
        st.success("Document parsed! Please review below.")

# --- Summary ---
summary = st.text_area("Summary", value=content.get("summary", ""))

# --- Steps ---
if 'steps' not in st.session_state:
    st.session_state['steps'] = content.get("steps", [])

if st.button("Add Step"):
    st.session_state['steps'].append({"text": "", "query": "", "screenshot": ""})

for idx, step in enumerate(st.session_state['steps']):
    st.session_state['steps'][idx]["text"] = st.text_input(f"Step {idx+1} Text", value=step["text"], key=f"step_text_{idx}")
    st.session_state['steps'][idx]["query"] = st.text_area(f"Step {idx+1} Query", value=step["query"], key=f"step_query_{idx}")
    uploaded_ss = st.file_uploader(f"Upload Screenshot for Step {idx+1}", type=['png', 'jpg'], key=f"step_ss_{idx}")
    if uploaded_ss and faq_entry:
        url = upload_screenshot(faq_entry["id"], idx+1, uploaded_ss)
        st.session_state['steps'][idx]["screenshot"] = url
    if step["screenshot"]:
        st.image(step["screenshot"], caption=f"Step {idx+1} Screenshot")

# --- Notes ---
notes = st.text_area("Notes", value=content.get("notes", ""))

# --- Gemini Validation ---
if st.button("Validate with Gemini") and selected_q:
    steps_text = "\n".join([f"Step {i+1}: {s['text']}" for i, s in enumerate(st.session_state['steps'])])
    with st.spinner("Validating..."):
        feedback = validate_with_gemini(selected_q, steps_text)
    st.subheader("Gemini Feedback")
    st.write(feedback)

# --- Generate Word Document ---
if st.button("Generate Word Document") and selected_q:
    doc = Document()
    doc.add_heading(selected_q, level=1)
    doc.add_heading("Summary", level=2)
    doc.add_paragraph(summary)
    doc.add_heading("Steps", level=2)
    for idx, step in enumerate(st.session_state['steps']):
        doc.add_paragraph(f"Step {idx+1}: {step['text']}")
        if step["query"]:
            doc.add_paragraph(f"Query: {step['query']}")
    doc.add_heading("Notes", level=2)
    doc.add_paragraph(notes)

    temp_stream = io.BytesIO()
    doc.save(temp_stream)
    temp_stream.seek(0)

    versions = faq_data.get("doc_versions", [])
    new_version = len(versions) + 1
    doc_url = upload_word_doc(faq_entry["id"], new_version, temp_stream)

    faq_data["content"] = {
        "summary": summary,
        "steps": st.session_state['steps'],
        "notes": notes
    }
    versions.append({
        "url": doc_url,
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z"
    })
    faq_data["doc_versions"] = versions
    save_faq_data(faq_entry["id"], faq_data)

    st.success("Word document generated and uploaded!")
    st.download_button("Download Latest Document", data=temp_stream.getvalue(),
                       file_name=f"FAQ_{selected_q}.docx",
                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
