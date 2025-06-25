import streamlit as st
from docx import Document as DocxDocument
from docx.shared import Inches
import datetime
import tempfile
import httpx
from supabase import create_client
import google.generativeai as genai
import re

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

def add_faq(question, assignee):
    data = {"question": question, "assignee": assignee}
    supabase.table("faqs_adv").insert({"data": data}).execute()

def upload_screenshot(faq_id, step_num, file):
    file_path = f"{faq_id}/step_{step_num}.png"
    url = f"{SUPABASE_URL}/storage/v1/object/faq-screenshots/{file_path}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "image/png",
        "x-upsert": "true"
    }
    response = httpx.post(url, headers=headers, content=file.getvalue())
    if response.status_code not in [200, 201]:
        st.error(f"Upload failed: {response.status_code}, {response.text}")
        return None
    return f"{SUPABASE_URL}/storage/v1/object/public/faq-screenshots/{file_path}?t={int(datetime.datetime.utcnow().timestamp())}"

def parse_uploaded_doc(doc_file):
    doc = DocxDocument(doc_file)
    content = {"summary": "", "steps": [], "notes": ""}
    current_section = None
    for p in doc.paragraphs:
        line = p.text.strip()
        if not line:
            continue

        if line == "[Summary]":
            current_section = "summary"
            continue
        elif line == "[Steps]":
            current_section = "steps"
            continue
        elif re.match(r"\[Step \d+\]", line):
            content["steps"].append({"text": "", "query": "", "screenshot": ""})
            continue
        elif line == "[Query Template]":
            current_section = "query"
            continue
        elif line == "[Screenshot]":
            current_section = "screenshot"
            continue
        elif line == "[Additional Notes]":
            current_section = "notes"
            continue

        if current_section == "summary":
            content["summary"] += line + " "
        elif current_section == "steps":
            if content["steps"]:
                content["steps"][-1]["text"] += line + " "
        elif current_section == "query":
            if content["steps"]:
                content["steps"][-1]["query"] += line + " "
        elif current_section == "screenshot":
            # marker only, no action needed; image handled separately
            continue
        elif current_section == "notes":
            content["notes"] += line + " "

    content["summary"] = content["summary"].strip()
    content["notes"] = content["notes"].strip()
    for step in content["steps"]:
        step["text"] = step["text"].strip()
        step["query"] = step["query"].strip()
    return content

def validate_with_gemini(question, steps_text):
    model = genai.GenerativeModel("gemini-2.5-flash")
    prompt = f"""The FAQ question is: "{question}".
This relates to an internal app for documenting troubleshooting steps.
Here are the steps:
{steps_text}
Please validate if these steps address the question correctly. Highlight gaps or irrelevant parts and suggest improvements."""
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
faq_map = {f["data"]["question"]: f for f in faqs if isinstance(f.get("data"), dict) and f["data"].get("question")}
questions = list(faq_map.keys())
assignees = list({f["data"]["assignee"] for f in faqs if isinstance(f.get("data"), dict) and f["data"].get("assignee")})

assignee = st.selectbox("Select Assignee", assignees, key="assignee_select") if assignees else None
faq_options = [q for q in questions if faq_map[q]["data"].get("assignee") == assignee] if assignee else []
selected_q = st.selectbox("Select FAQ", faq_options, key="faq_select") if faq_options else None

faq_entry = faq_map.get(selected_q)
faq_data = faq_entry["data"] if faq_entry else {}
content = faq_data.get("content", {})

# Reset state on FAQ change
if 'last_selected_q' not in st.session_state:
    st.session_state['last_selected_q'] = None

if selected_q != st.session_state['last_selected_q']:
    st.session_state['steps'] = content.get("steps", [])
    st.session_state['summary'] = content.get("summary", "")
    st.session_state['notes'] = content.get("notes", "")
    st.session_state['pending_screenshots'] = {}
    st.session_state['parsed_doc'] = False
    st.session_state['uploaded_doc'] = None
    st.session_state['last_selected_q'] = selected_q

uploaded_doc = st.file_uploader("Upload Word Document", type="docx", key="doc_upload")
if uploaded_doc and not st.session_state['parsed_doc']:
    parsed = parse_uploaded_doc(uploaded_doc)
    st.session_state['steps'] = parsed.get("steps", [])
    st.session_state['summary'] = parsed.get("summary", "")
    st.session_state['notes'] = parsed.get("notes", "")
    st.session_state['parsed_doc'] = True
    st.session_state['uploaded_doc'] = None  # Clear after parse
    st.success("Document parsed! Review below.")
    with st.expander("🔍 Parsed Document JSON"):
        st.json(parsed)

summary = st.text_area("Summary", value=st.session_state.get("summary", ""))
notes = st.text_area("Notes", value=st.session_state.get("notes", ""))

if st.button("Add Step"):
    st.session_state['steps'].append({"text": "", "query": "", "screenshot": ""})

for idx, step in enumerate(st.session_state['steps']):
    st.session_state['steps'][idx]["text"] = st.text_input(f"Step {idx+1} Text", value=step["text"], key=f"text_{idx}_{selected_q}")
    st.session_state['steps'][idx]["query"] = st.text_area(f"Step {idx+1} Query", value=step["query"], key=f"query_{idx}_{selected_q}")
    uploaded_ss = st.file_uploader(f"Upload Screenshot for Step {idx+1}", type=["png", "jpg"], key=f"ss_{idx}_{selected_q}")
    if uploaded_ss:
        st.session_state['pending_screenshots'][idx+1] = uploaded_ss
        st.image(uploaded_ss, caption=f"Pending upload Step {idx+1} Screenshot", width=300)
    elif step["screenshot"]:
        st.image(step["screenshot"], caption=f"Saved Step {idx+1} Screenshot", width=300)

if st.button("💾 Save / Update FAQ in DB"):
    for step_num, file in st.session_state['pending_screenshots'].items():
        url = upload_screenshot(faq_entry["id"], step_num, file)
        if url:
            st.session_state['steps'][step_num-1]["screenshot"] = url
    updated_data = {
        "question": selected_q,
        "assignee": faq_entry["data"].get("assignee"),
        "content": {
            "summary": summary,
            "steps": st.session_state["steps"],
            "notes": notes
        }
    }
    supabase.table("faqs_adv").update({"data": updated_data, "updated_at": "now()"}).eq("id", faq_entry["id"]).execute()
    st.success("✅ FAQ updated and saved in DB!")

if st.button("📄 Generate FAQ Document"):
    doc = DocxDocument()
    doc.add_heading('FAQ Document', level=1)
    doc.add_paragraph(f"Question: {selected_q}")
    doc.add_paragraph("[Summary]")
    doc.add_paragraph(st.session_state["summary"])
    doc.add_paragraph("[Steps]")
    for i, step in enumerate(st.session_state['steps']):
        doc.add_paragraph(f"[Step {i+1}]")
        doc.add_paragraph(step['text'])
        if step['query']:
            doc.add_paragraph("[Query Template]")
            doc.add_paragraph(step['query'])
        if step['screenshot']:
            doc.add_paragraph("[Screenshot]")
            tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
            response = httpx.get(step['screenshot'])
            tmp_file.write(response.content)
            tmp_file.flush()
            doc.add_picture(tmp_file.name, width=Inches(4))
    doc.add_paragraph("[Additional Notes]")
    doc.add_paragraph(st.session_state["notes"])
    tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix='.docx')
    doc.save(tmp_out.name)
    st.success("✅ FAQ document generated!")
    st.download_button("📥 Download FAQ Document", data=open(tmp_out.name, 'rb').read(),
                       file_name='FAQ_Generated.docx',
                       mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document')

if st.button("Validate with Gemini") and selected_q:
    steps_text = "\n".join([f"Step {i+1}: {s['text']}" for i, s in enumerate(st.session_state['steps'])])
    with st.spinner("Validating..."):
        feedback = validate_with_gemini(selected_q, steps_text)
    st.subheader("Gemini Feedback")
    st.write(feedback)
