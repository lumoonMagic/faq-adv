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
    resp = supabase.table("faqs").select("*").execute()
    return resp.data if resp.data else []

def save_faq_data(faq_id, data):
    supabase.table("faqs").update({"data": data, "updated_at": "now()"}).eq("id", faq_id).execute()

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
            step_num = len(content["steps"]) + 1
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
st.title("📄 FAQ Generator + Validator")

faqs = load_faqs()
questions = [
    f["data"].get("question", "Unnamed FAQ") 
    for f in faqs 
    if f.get("data") and isinstance(f["data"], dict)
]
faq_map = {
    f["data"].get("question", f["id"]): f
    for f in faqs
    if f.get("data") and isinstance(f["data"], dict)
}

faq_map = {f["data"]["question"]: f for f in faqs}

assignees = list(set(json.loads(f["data"])["assignee"] for f in faqs))
assignee = st.selectbox("Select Assignee", assignees)
faq_options = [q for q in questions if json.loads(faq_map[q]["data"])["assignee"] == assignee]
selected_q = st.selectbox("Select FAQ", faq_options)

faq_entry = faq_map.get(selected_q)
faq_data = json.loads(faq_entry["data"]) if faq_entry else {}
content = faq_data.get("content", {})

# Upload existing Word doc if no content
if not content:
    st.info("No structured data found for this FAQ. Upload a previously generated Word document to extract info.")
    uploaded_doc = st.file_uploader("Upload Existing FAQ Word Document", type="docx")
    if uploaded_doc:
        content = parse_uploaded_doc(uploaded_doc)
        st.success("Document parsed! Please review below.")

summary = st.text_area("Summary", value=content.get("summary", ""))

# Steps
if 'steps' not in st.session_state:
    st.session_state['steps'] = content.get("steps", [])

if st.button("Add Step"):
    st.session_state['steps'].append({"text": "", "query": "", "screenshot": ""})

for idx, step in enumerate(st.session_state['steps']):
    st.session_state['steps'][idx]["text"] = st.text_input(f"Step {idx+1} Text", value=step["text"], key=f"step_text_{idx}")
    st.session_state['steps'][idx]["query"] = st.text_area(f"Step {idx+1} Query", value=step["query"], key=f"step_query_{idx}")
    uploaded_ss = st.file_uploader(f"Upload Screenshot for Step {idx+1}", type=['png', 'jpg'], key=f"step_ss_{idx}")
    if uploaded_ss:
        url = upload_screenshot(faq_entry["id"], idx+1, uploaded_ss)
        st.session_state['steps'][idx]["screenshot"] = url
    if step["screenshot"]:
        st.image(step["screenshot"], caption=f"Step {idx+1} Screenshot")

notes = st.text_area("Notes", value=content.get("notes", ""))

# Gemini Validation
if st.button("Validate with Gemini"):
    steps_text = "\n".join([f"Step {i+1}: {s['text']}" for i, s in enumerate(st.session_state['steps'])])
    with st.spinner("Validating..."):
        feedback = validate_with_gemini(selected_q, steps_text)
    st.subheader("Gemini Feedback")
    st.write(feedback)

# Generate Word Doc
if st.button("Generate Word Document"):
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

    # Determine version
    versions = faq_data.get("doc_versions", [])
    new_version = len(versions) + 1
    doc_url = upload_word_doc(faq_entry["id"], new_version, temp_stream)

    # Update DB
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
    save_faq_data(faq_entry["id"], json.dumps(faq_data))

    st.success("Word document generated and uploaded!")
    st.download_button("Download Latest Document", data=temp_stream.getvalue(), file_name=f"FAQ_{selected_q}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
