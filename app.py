import os
import json
import re
import threading
import sys
from urllib.parse import urlparse
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
print(f"GROQ_API_KEY set: {bool(os.getenv('GROQ_API_KEY'))}", flush=True)
print(f"SECRET set: {bool(os.getenv('SECRET'))}", flush=True)

client = Groq(api_key=os.getenv('GROQ_API_KEY'))

def get_driver():
    print("[DRIVER] Creating Chrome driver...", flush=True)
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.binary_location = os.getenv('CHROME_BIN', '/usr/bin/chromium')
    
    service = Service(executable_path=os.getenv('CHROMEDRIVER_PATH', '/usr/bin/chromedriver'))
    driver = webdriver.Chrome(service=service, options=chrome_options)
    print("[DRIVER] Chrome driver created!", flush=True)
    return driver

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
        
        prompt = f"""You are solving a data analysis quiz.

Page content:
{content}

HTML:
{html[:10000]}

Your task:
1. Understand the question being asked
2. Find the submit URL (could be relative like "/submit")
3. ACTUALLY SOLVE the question - calculate, scrape, or extract the real answer

Return ONLY valid JSON:
{{"submit_url": "/submit", "answer": THE_ACTUAL_ANSWER}}

IMPORTANT: Provide the REAL answer, not a description."""

        print("[SOLVE] Calling Groq API...", flush=True)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        response_text = response.choices[0].message.content
        print(f"[SOLVE] LLM Response: {response_text}", flush=True)
        
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if not json_match:
            raise ValueError(f"No JSON found")
        
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
    print(f"[PROCESS] Email: {email}", flush=True)
    
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
            sys.stdout.flush()
            break
    
    print("[PROCESS] ===== QUIZ COMPLETE =====", flush=True)

@app.route('/quiz', methods=['POST'])
def quiz_endpoint():
    print("[ENDPOINT] Received POST /quiz", flush=True)
    try:
        data = request.get_json()
        print(f"[ENDPOINT] Data: {data}", flush=True)
        
        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400
        
        email = data.get('email')
        secret = data.get('secret')
        url = data.get('url')
        
        if not email or not secret or not url:
            return jsonify({'error': 'Missing required fields'}), 400
        
        if secret != os.getenv('SECRET'):
            print(f"[ENDPOINT] Secret mismatch!", flush=True)
            return jsonify({'error': 'Invalid secret'}), 403
        
        print(f"[ENDPOINT] Starting background thread...", flush=True)
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
