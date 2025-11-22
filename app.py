import os
import json
import re
import threading
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

def solve_quiz(quiz_url):
    driver = get_driver()
    
    try:
        driver.get(quiz_url)
        time.sleep(3)
        
        content = driver.find_element(By.TAG_NAME, 'body').text
        html = driver.page_source
        driver.quit()
        
        prompt = f"""You are solving a data analysis quiz.

Page content:
{content}

HTML:
{html[:10000]}

Your task:
1. Understand the question being asked
2. Find the submit URL (could be relative like "/submit")
3. ACTUALLY SOLVE the question - calculate, scrape, or extract the real answer

If asked to scrape a hidden element, find it in the HTML.
If asked to calculate something, do the math.
If there's an audio/file URL, note it.

Return ONLY valid JSON:
{{"submit_url": "/submit", "answer": THE_ACTUAL_ANSWER}}

IMPORTANT: Provide the REAL answer, not a description of what to do."""

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        response_text = response.choices[0].message.content
        print(f"LLM Response: {response_text}")
        
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if not json_match:
            raise ValueError(f"No JSON found")
        
        result = json.loads(json_match.group(0))
        return result
        
    except Exception as e:
        driver.quit()
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
    print(f"Submitting to: {submit_url}")
    response = requests.post(submit_url, json=payload, timeout=30)
    return response.json()

def process_quiz(start_url, email, secret):
    current_url = start_url
    max_iterations = 15
    iteration = 0
    
    while current_url and iteration < max_iterations:
        iteration += 1
        print(f"\n{'='*50}")
        print(f"Solving quiz {iteration}: {current_url}")
        
        try:
            result = solve_quiz(current_url)
            submit_url = result['submit_url']
            answer = result['answer']
            
            print(f"Answer: {answer}")
            
            submission_result = submit_answer(submit_url, email, secret, current_url, answer)
            print(f"Result: {submission_result}")
            
            if submission_result.get('correct'):
                print("✓ Correct!")
            else:
                print(f"✗ Wrong: {submission_result.get('reason')}")
            
            current_url = submission_result.get('url')
            
            if submission_result.get('delay'):
                time.sleep(submission_result['delay'])
                
        except Exception as e:
            print(f"Error: {e}")
            break
    
    print("\nQuiz processing complete")

@app.route('/quiz', methods=['POST'])
def quiz_endpoint():
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
        return jsonify({'error': str(e)}), 400

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'}), 200

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)