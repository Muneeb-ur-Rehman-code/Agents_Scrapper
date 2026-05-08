from __future__ import annotations
# ============================================================
#  anti_detection.py  —  Human-like browser interaction helpers
# ============================================================
import time, random, logging
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.remote.webdriver import WebDriver

logger = logging.getLogger("AgentScraper")


def human_sleep(mn=0.3, mx=1.5):
    time.sleep(random.uniform(mn, mx))

def long_sleep(mn=2.0, mx=4.0):
    time.sleep(random.uniform(mn, mx))

def human_move_to(driver, element):
    try:
        ac = ActionChains(driver)
        ac.move_to_element_with_offset(
            element, random.randint(-4,4), random.randint(-4,4))
        ac.perform()
        human_sleep(0.1, 0.3)
    except Exception as e:
        logger.debug(f"move_to: {e}")

def human_click(driver, element):
    human_move_to(driver, element)
    human_sleep(0.1, 0.25)
    element.click()
    human_sleep(0.2, 0.5)

def human_type(element, text):
    for ch in text:
        element.send_keys(ch)
        d = random.uniform(0.05, 0.20)
        if random.random() < 0.07:
            d += random.uniform(0.15, 0.40)
        time.sleep(d)

def random_scroll(driver, times=3):
    for _ in range(times):
        dy = random.randint(200, 500) * random.choice([1,-1])
        driver.execute_script(
            "window.scrollBy({top:arguments[0],behavior:'smooth'});", dy)
        human_sleep(0.4, 1.0)

def scroll_to_element(driver, element):
    driver.execute_script(
        "arguments[0].scrollIntoView({behavior:'smooth',block:'center'});",
        element)
    human_sleep(0.3, 0.6)