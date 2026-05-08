import undetected_chromedriver as uc
import time

options = uc.ChromeOptions()
options.add_argument('--headless')
driver = uc.Chrome(options=options)
try:
    driver.get('https://www.zillow.com/professionals/real-estate-agent-reviews/')
    time.sleep(10)
    print(f"Current URL: {driver.current_url}")
    
    inputs = driver.find_elements('tag name', 'input')
    for i, inp in enumerate(inputs):
        try:
            print(f"Input {i}: id='{inp.get_attribute('id')}', placeholder='{inp.get_attribute('placeholder')}', aria-label='{inp.get_attribute('aria-label')}'")
        except: pass
    
    buttons = driver.find_elements('tag name', 'button')
    for i, btn in enumerate(buttons):
        try:
            print(f"Button {i}: id='{btn.get_attribute('id')}', text='{btn.text}', aria-label='{btn.get_attribute('aria-label')}'")
        except: pass
finally:
    driver.quit()
