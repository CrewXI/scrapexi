
import json
import os
import re

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import Page


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

def call_gemini_api(prompt: str, api_key: str, model_name: str = "gemini-2.0-flash-exp", response_schema: bool = False) -> str:
    """
    Calls Google Gemini REST API directly to avoid heavy SDK dependencies.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    
    headers = {
        "Content-Type": "application/json"
    }
    
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }

    if response_schema:
        payload["generationConfig"] = {
            "response_mime_type": "application/json"
        }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        
        # Extract text from response
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
             # Check for safety block
             if "promptFeedback" in data and "blockReason" in data["promptFeedback"]:
                 raise Exception(f"Blocked by safety filters: {data['promptFeedback']['blockReason']}")
             raise Exception(f"Unexpected API response format: {data}")

    except requests.exceptions.RequestException as e:
        if hasattr(e, 'response') and e.response is not None:
            raise Exception(f"Gemini API Error {e.response.status_code}: {e.response.text}")
        raise e

def query_data_with_gemini(page: Page, query: str, model_name: str = "gemini-2.0-flash-exp") -> dict:
    """
    Uses Google Gemini to extract data from the page based on a GraphQL-like query.
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is not set")
    
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
        response_text = call_gemini_api(prompt, api_key, model_name, response_schema=True)
        
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
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        # Ideally shouldn't happen if called from main flow
        return None

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
        selector = call_gemini_api(prompt, api_key, model_name, response_schema=False)
        selector = selector.strip().replace("`", "")
        if "NONE" in selector or not selector:
            return None
        return selector
    except Exception as e:
        print(f"Next Page Detection Error: {e}")
        return None
