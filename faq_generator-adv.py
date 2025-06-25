def parse_uploaded_doc(doc_file):
    doc = Document(doc_file)
    content = {"summary": "", "steps": [], "notes": ""}
    current_section = None

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        lower_text = text.lower()

        # Detect section headers
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

        # Add to the right section
        if current_section == "summary":
            # Stop if a new section starts
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

    # Final cleanup
    content["summary"] = content["summary"].strip()
    content["notes"] = content["notes"].strip()
    return content
