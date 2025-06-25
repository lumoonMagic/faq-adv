import streamlit as st
from docx import Document
import datetime
import re
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
    doc = Document(doc_file)
    content = {"summary": "", "steps": [], "notes": ""}
    lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    
    current_section = None

    for line in lines:
        lower = line.lower()

        if lower == "summary":
            current_section = "summary"
            continue
        elif lower == "steps":
            current_section = "steps"
            continue
        elif lower.startswith("step"):
            current_section = "steps"
            content["steps"].append({"text": line, "query": "", "screenshot": ""})
            continue
        elif lower.startswith("additional notes"):
            current_section = "notes"
            continue

        if current_section == "summary":
            content["summary"] += line + " "
        elif current_section == "steps":
            if content["steps"]:
                if lower.startswith("query template"):
                    content["steps"][-1]["query"] += " " + line
                elif "screenshot" in lower:
                    continue
                else:
                    content["steps"][-1]["text"] += " " + line
        elif current_section == "notes":
            content["notes"] += line + " "

    content["summary"] = content["summary"].strip()
    content["notes"] = content["notes"].strip()
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
        st.session_state['summary'] = content.get("summary", "")
        st.session_state['notes'] = content.get("notes", "")
        st.session_state['parsed_doc'] = False
        st.session_state['current_faq_id'] = current_id

uploaded_doc = st.file_uploader("Upload Word Document", type="docx")
if uploaded_doc and not st.session_state['parsed_doc']:
    parsed = parse_uploaded_doc(uploaded_doc)
    st.session_state['steps'] = parsed.get("steps", [])
    st.session_state['parsed_summary'] = parsed.get("summary", "")
    st.session_state['parsed_notes'] = parsed.get("notes", "")
    st.session_state['parsed_doc'] = True
    st.success("Document parsed! Review below.")
    st.write(f"✅ Parsed Summary Preview:\n{st.session_state['parsed_summary']}")
    with st.expander("🔍 Parsed Document JSON"):
        st.json(parsed)

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
    st.session_state['steps'] = []

if st.button("Add Step"):
    st.session_state['steps'].append({"text": "", "query": "", "screenshot": ""})

for idx, step in enumerate(st.session_state['steps']):
    st.session_state['steps'][idx]["text"] = st.text_input(f"Step {idx+1} Text", value=step["text"])
    st.session_state['steps'][idx]["query"] = st.text_area(f"Step {idx+1} Query", value=step["query"])
    uploaded_ss = st.file_uploader(f"Upload Screenshot for Step {idx+1}", type=["png", "jpg"], key=f"ss_{idx}")
    if uploaded_ss:
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
