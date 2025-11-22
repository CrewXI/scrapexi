
import os
import json
import re
from bs4 import BeautifulSoup
import google.generativeai as genai
from playwright.sync_api import Page

# Configure Gemini
# Expects GOOGLE_API_KEY in environment variables
if os.getenv("GOOGLE_API_KEY"):
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

def clean_html(html_content: str) -> str:
    """
    Simplifies HTML to reduce token count for the LLM.
    Removes scripts, styles, and comments.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    
    # Remove script and style elements
    for script in soup(["script", "style", "svg", "path", "noscript", "iframe"]):
        script.decompose()

    # Get text and attributes, keeping structure
    return str(soup.body)

def clean_json_string(json_str: str) -> str:
    """
    Attempts to fix common JSON truncation or formatting issues from LLM output.
    """
    json_str = json_str.strip()
    
    # Check if it was truncated
    if not json_str.endswith("}") and not json_str.endswith("]"):
        # Attempt to close open braces/brackets
        open_braces = json_str.count("{")
        close_braces = json_str.count("}")
        open_brackets = json_str.count("[")
        close_brackets = json_str.count("]")
        
        json_str += "}" * (open_braces - close_braces)
        json_str += "]" * (open_brackets - close_brackets)
        
    return json_str

def query_data_with_gemini(page: Page, query: str, model_name: str = "gemini-2.0-flash-exp") -> dict:
    """
    Uses Google Gemini to extract data from the page based on a GraphQL-like query.
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is not set")
    
    # Configure on every call to ensure we have the latest env var
    genai.configure(api_key=api_key)

    raw_html = page.content()
    cleaned_html = clean_html(raw_html)
    
    prompt = f"""
    You are an expert web scraper. I will provide you with the HTML of a webpage and a query describing what data to extract.
    
    The query is in a GraphQL-like format or plain text description.
    Your job is to find the data in the HTML and return it as a strictly valid JSON object.
    
    Query:
    {query}
    
    Rules:
    1. Return ONLY valid JSON. No markdown formatting, no explanation.
    2. If the query asks for a list (e.g., products[]), return an array of objects.
    3. Map the field names in the query to the keys in your JSON.
    4. If data is not found, return null for that field.
    
    HTML Context:
    {cleaned_html}
    """

    try:
        # Set response_mime_type to application/json to force structured output
        generation_config = {"response_mime_type": "application/json"}
        model = genai.GenerativeModel(model_name, generation_config=generation_config)
        
        response = model.generate_content(prompt)
        response_text = response.text
        
        # Clean up any markdown blocks if the model still outputs them
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
             response_text = response_text.split("```")[1].split("```")[0]
        
        # Attempt to clean/fix truncated JSON
        response_text = clean_json_string(response_text)
             
        return json.loads(response_text)
        
    except Exception as e:
        print(f"Gemini Extraction Error: {e}")
        
        error_msg = str(e)
        # Check for JSON decoding error which likely means truncation
        if "Unterminated string" in error_msg or "Expecting value" in error_msg:
            # Analyze the raw response text to find the last key
            if 'response_text' in locals():
                # Look for patterns like "key_name":
                # We search from the end of the string
                matches = list(re.finditer(r'"([^"]+)":', response_text))
                if matches:
                    last_key = matches[-1].group(1)
                    error_msg += f" (HINT: The response was likely truncated while processing the field '{last_key}'. Try removing it from your schema.)"
        
        return {"error": error_msg}

def find_next_page_element(page: Page, model_name: str = "gemini-2.0-flash-exp") -> str | None:
    """
    Asks Gemini to identify the CSS selector for the 'Next Page' button/link.
    """
    raw_html = page.content()
    cleaned_html = clean_html(raw_html)[:30000] # Limit context for selector finding
    
    prompt = f"""
    Analyze this HTML and find the "Next Page" button or link for pagination.
    Return ONLY the CSS selector that uniquely identifies the 'Next' button.
    If there is no next page button (or it's disabled), return the string "NONE".
    
    Do not return markdown. Just the selector string (e.g. ".pagination > a.next" or "#next-btn").
    
    HTML Context:
    {cleaned_html}
    """
    
    try:
        # Ensure configured
        api_key = os.getenv("GOOGLE_API_KEY")
        if api_key: genai.configure(api_key=api_key)
        
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(prompt)
        selector = response.text.strip().replace("`", "")
        if "NONE" in selector or not selector:
            return None
        return selector
    except Exception as e:
        print(f"Next Page Detection Error: {e}")
        return None

