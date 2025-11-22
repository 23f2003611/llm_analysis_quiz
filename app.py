import os
import json
import re
import threading
import sys
from urllib.parse import urlparse, urljoin
from flask import Flask, request, jsonify
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
import time
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

print("=== SERVER STARTING ===", flush=True)
client = Groq(api_key=os.getenv('GROQ_API_KEY'))

def get_driver():
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.binary_location = os.getenv('CHROME_BIN', '/usr/bin/chromium')
    service = Service(executable_path=os.getenv('CHROMEDRIVER_PATH', '/usr/bin/chromedriver'))
    return webdriver.Chrome(service=service, options=chrome_options)

def fetch_linked_resources(base_url, html, content):
    """Fetch any linked resources mentioned in the page"""
    resources = {}
    
    # Find URLs to scrape (relative or absolute)
    urls_to_fetch = []
    
    # Look for scrape instructions or data URLs
    patterns = [
        r'href=["\']([^"\']+)["\']',
        r'Scrape\s+<?a?\s*(?:href=["\'])?([^\s"\'<>]+)',
        r'download[^\s]*\s+([^\s]+)',
        r'CSV file[^\n]*href=["\']([^"\']+)["\']',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, html + content, re.IGNORECASE)
        urls_to_fetch.extend(matches)
    
    # Fetch each unique URL
    seen = set()
    for url in urls_to_fetch:
        if url in seen or url.startswith('#') or url.startswith('javascript'):
            continue
        seen.add(url)
        
        # Make absolute URL
        if url.startswith('/'):
            parsed = urlparse(base_url)
            full_url = f"{parsed.scheme}://{parsed.netloc}{url}"
        elif not url.startswith('http'):
            full_url = urljoin(base_url, url)
        else:
            full_url = url
        
        # Skip submit URLs
        if 'submit' in full_url.lower():
            continue
            
        try:
            print(f"[FETCH] Fetching resource: {full_url}", flush=True)
            resp = requests.get(full_url, timeout=15)
            resources[full_url] = resp.text[:10000]
            print(f"[FETCH] Got {len(resp.text)} chars: {resp.text[:200]}", flush=True)
        except Exception as e:
            print(f"[FETCH] Error fetching {full_url}: {e}", flush=True)
    
    return resources

def calculate_from_csv(csv_text, cutoff=None):
    """Parse CSV and calculate sum of numbers above cutoff"""
    try:
        lines = csv_text.strip().split('\n')
        numbers = []
        for line in lines:
            for val in line.split(','):
                val = val.strip()
                try:
                    numbers.append(float(val))
                except:
                    pass
        
        if cutoff is not None:
            numbers = [n for n in numbers if n > cutoff]
        
        return sum(numbers)
    except Exception as e:
        print(f"[CALC] Error: {e}", flush=True)
        return None

def solve_quiz(quiz_url):
    print(f"[SOLVE] Fetching URL: {quiz_url}", flush=True)
    driver = get_driver()
    
    try:
        driver.get(quiz_url)
        time.sleep(3)
        
        content = driver.find_element(By.TAG_NAME, 'body').text
        html = driver.page_source
        driver.quit()
        
        print(f"[SOLVE] Page content: {content[:500]}", flush=True)
        
        # Fetch any linked resources
        resources = fetch_linked_resources(quiz_url, html, content)
        
        # Try to auto-calculate if it's a CSV/numbers task
        calculated_answer = None
        cutoff_match = re.search(r'[Cc]utoff[:\s]+(\d+)', content)
        cutoff = int(cutoff_match.group(1)) if cutoff_match else None
        
        for url, data in resources.items():
            if '.csv' in url or any(c.isdigit() for c in data[:100]):
                if cutoff:
                    calculated_answer = calculate_from_csv(data, cutoff)
                    print(f"[CALC] Sum above cutoff {cutoff}: {calculated_answer}", flush=True)
        
        resources_text = ""
        if resources:
            resources_text = "\n\nFETCHED RESOURCES:\n"
            for url, data in resources.items():
                resources_text += f"\n--- {url} ---\n{data}\n"
        
        if calculated_answer is not None:
            resources_text += f"\n\nPRE-CALCULATED: Sum of numbers above cutoff {cutoff} = {calculated_answer}"
        
        prompt = f"""You are solving a data analysis quiz. You MUST provide a concrete answer.

PAGE CONTENT:
{content}

HTML (partial):
{html[:5000]}
{resources_text}

RULES:
1. If FETCHED RESOURCES contains a short text/code - THAT IS THE ANSWER (the secret code)
2. If PRE-CALCULATED sum is provided - USE THAT NUMBER as the answer
3. For secret codes - return the exact string from FETCHED RESOURCES
4. Always return a specific value, never a description

Return ONLY this JSON (no other text):
{{"submit_url": "/submit", "answer": THE_VALUE}}"""

        print("[SOLVE] Calling Groq API...", flush=True)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        response_text = response.choices[0].message.content
        print(f"[SOLVE] LLM Response: {response_text[:500]}", flush=True)
        
        json_match = re.search(r'\{[^{}]*"submit_url"[^{}]*"answer"[^{}]*\}', response_text, re.DOTALL)
        if not json_match:
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if not json_match:
            raise ValueError("No JSON found")
        
        result = json.loads(json_match.group(0))
        return result
        
    except Exception as e:
        print(f"[SOLVE] Error: {e}", flush=True)
        try:
            driver.quit()
        except:
            pass
        raise e

def submit_answer(submit_url, email, secret, quiz_url, answer):
    if submit_url.startswith('/'):
        parsed = urlparse(quiz_url)
        submit_url = f"{parsed.scheme}://{parsed.netloc}{submit_url}"
    
    payload = {
        'email': email,
        'secret': secret,
        'url': quiz_url,
        'answer': answer
    }
    print(f"[SUBMIT] URL: {submit_url}", flush=True)
    print(f"[SUBMIT] Answer: {answer}", flush=True)
    response = requests.post(submit_url, json=payload, timeout=30)
    return response.json()

def process_quiz(start_url, email, secret):
    print(f"[PROCESS] ===== STARTING QUIZ =====", flush=True)
    print(f"[PROCESS] URL: {start_url}", flush=True)
    
    current_url = start_url
    max_iterations = 15
    iteration = 0
    
    while current_url and iteration < max_iterations:
        iteration += 1
        print(f"\n[QUIZ {iteration}] {current_url}", flush=True)
        
        try:
            result = solve_quiz(current_url)
            submit_url = result['submit_url']
            answer = result['answer']
            
            submission_result = submit_answer(submit_url, email, secret, current_url, answer)
            print(f"[RESULT] {submission_result}", flush=True)
            
            if submission_result.get('correct'):
                print("✓ Correct!", flush=True)
            else:
                print(f"✗ Wrong: {submission_result.get('reason')}", flush=True)
            
            current_url = submission_result.get('url')
            
            if submission_result.get('delay'):
                time.sleep(submission_result['delay'])
                
        except Exception as e:
            print(f"[ERROR] {e}", flush=True)
            import traceback
            traceback.print_exc()
            break
    
    print("[PROCESS] ===== QUIZ COMPLETE =====", flush=True)

@app.route('/quiz', methods=['POST'])
def quiz_endpoint():
    print("[ENDPOINT] Received POST /quiz", flush=True)
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400
        
        email = data.get('email')
        secret = data.get('secret')
        url = data.get('url')
        
        if not email or not secret or not url:
            return jsonify({'error': 'Missing required fields'}), 400
        
        if secret != os.getenv('SECRET'):
            return jsonify({'error': 'Invalid secret'}), 403
        
        thread = threading.Thread(target=process_quiz, args=(url, email, secret))
        thread.start()
        
        return jsonify({'status': 'processing'}), 200
        
    except Exception as e:
        print(f"[ENDPOINT] Error: {e}", flush=True)
        return jsonify({'error': str(e)}), 400

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'}), 200

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    print(f"=== Starting server on port {port} ===", flush=True)
    app.run(host='0.0.0.0', port=port)
