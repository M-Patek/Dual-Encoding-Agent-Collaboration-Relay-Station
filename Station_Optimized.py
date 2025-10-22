import time
import sys
import random as global_random
import os
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError, expect

# =======================================================================================
# === I. 阶段零：前置依赖与全局常量规范 (Phase Zero: Dependencies & Constants) ===
# =======================================================================================

TERMINATION_PHRASE = "TASK_COMPLETED_SUCCESSFULLY"
START_CMD_MSG = "请启动双编码协作流程并开始您的协调任务。"

# 【ULTIMATE ANTI-BOT STRATEGY v9.2: PRECISE LOCKING】
# 终极反-反制策略 v9.2：精准锁定
# 核心问题：THINKING_INDICATOR_SEL 不够精确，匹配到多个元素，导致 strict mode violation。
# 新策略：
# 1. 更新 THINKING_INDICATOR_SEL 为更精确的 '.bard-avatar.thinking'。
# 2. 修改 wait_for_ai_response 逻辑，使其能处理找到多个指示器的情况，等待所有指示器都消失。

INPUT_SEL = 'div[role="textbox"]'
CHAT_AREA_SEL = 'body'
MESSAGE_ANCHOR_SEL = '.response-container'

# 更新为更精确的定位器，优先锁定代表AI头像思考状态的元素
THINKING_INDICATOR_SEL = '.bard-avatar.thinking'
# 保留一个备用定位器，以防头像状态变化
FALLBACK_THINKING_SEL = '[class*="loading"], [class*="generating"]'


SEND_BUTTON_SEL = 'button mat-icon[data-mat-icon-name="send"]'
# ---------------------------------------------------------------------------------------


# =======================================================================================
# === Ib. 阶段零-B：全局配置 (Phase Zero-B: Global Configuration) =====================
# =======================================================================================
class Config:
    """集中管理所有可配置的全局变量。"""
    EDGE_USER_DATA_PATH = "C:\\Users\\asus\\AppData\\Local\\Microsoft\\Edge\\User Data"
    GEMINI_URL_A = "https://gemini.google.com/u/1/app?hl=zh-cn"
    GEMINI_URL_B = "https://gemini.google.com/u/3/app?hl=zh-cn"

    class Timeouts:
        PAGE_LOAD_MS = 90000
        PAGE_STABILITY_MS = 30000
        AI_GENERATION_MS = 120000 # 等待“思考中”动画消失的最长时间
        WAIT_FOR_CHANGE_MS = 180000
        MANUAL_SETUP_SEC = 30
        CONTENT_STABILITY_MS = 1500

class Session:
    """封装会话状态和人格化参数。"""
    def __init__(self, seed: int):
        self.session_seed = seed
        self.rng = global_random.Random(seed)
        self.task_lock = False
        personas = {
            'new_user': {'P_IDLE_TRIGGER': 0.30},
            'experienced_user': {'P_IDLE_TRIGGER': 0.15}
        }
        self.persona_name = self.rng.choice(list(personas.keys()))
        self.behavioral_params = personas[self.persona_name]
        log("INFO", f"会话已初始化，种子: {self.session_seed}", "会话")
        log("INFO", f"L1人格已分配: '{self.persona_name}'", "L1人格")

# =======================================================================================
# === II. 阶段一：核心功能函数封装 (Phase One: Core Function Wrappers) ===
# =======================================================================================

def log(level: str, message: str, step: str = "协调器"):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}][{level:<7}][{step:<15}] {message}")

def wait_for_page_stability(page: Page, agent_name: str):
    log("INFO", f"等待 {agent_name} 页面稳定 (超时 {Config.Timeouts.PAGE_STABILITY_MS//1000}s)...", "页面等待")
    try:
        expect(page.locator(INPUT_SEL)).to_be_editable(timeout=Config.Timeouts.PAGE_STABILITY_MS)
        log("SUCCESS", f"{agent_name} 页面已稳定，输入框可交互。", "页面等待")
    except PlaywrightTimeoutError:
        log("FATAL", f"{agent_name} 页面稳定失败，未找到可交互的输入框。", "页面等待")
        raise Exception("页面初始化失败，无法继续。")

# 新函数：只等待初步的页面变化，不做复杂判断
def wait_for_initial_change(page: Page, previous_html: str, previous_anchor_count: int):
    log("INFO", f"等待初步响应...", "初步响应确认")
    js_wait_expression = """
        (args) => {
            const [selector, prevLength, prevCount, anchorSelector] = args;
            const container = document.querySelector(selector);
            if (!container) return false;
            const currentLength = container.innerHTML.length;
            const currentCount = document.querySelectorAll(anchorSelector).length;
            // 混合感知：DOM脉冲 或 锚点增加 (只要有一个变化就立刻返回)
            return currentLength !== prevLength || currentCount > prevCount;
        }
    """
    try:
        page.wait_for_function(
            js_wait_expression,
            arg=[CHAT_AREA_SEL, len(previous_html), previous_anchor_count, MESSAGE_ANCHOR_SEL],
            timeout=30000 # 等待初步响应的时间可以短一些
        )
        log("SUCCESS", f"检测到初步响应！", "初步响应确认")
        return True # 表示检测到变化
    except PlaywrightTimeoutError:
        log("WARNING", f"等待初步响应超时。", "初步响应确认")
        return False # 表示未检测到变化

# 升级后的函数：专门等待AI的完整回复（兼容Pro模式）
def wait_for_ai_response(page: Page, agent_name: str, previous_html: str, previous_anchor_count: int):
    log("INFO", f"开始等待 {agent_name} 的AI响应 (Pro模式兼容)...", "AI响应感知")
    # 第一重确认：等待任何形式的响应（思考动画或直接答案）
    if not wait_for_initial_change(page, previous_html, previous_anchor_count):
        # 如果连初步响应都没有，可能出错了，直接抛出异常或返回
        log("ERROR", "未检测到初步响应，AI可能未回复。")
        raise Exception(f"{agent_name} 未在指定时间内响应。")

    log("INFO", f"检测到初步响应，开始第二重确认...", "AI响应感知")

    # 第二重确认：检查是否存在“思考中”动画 (更鲁棒的方式)
    thinking_indicators = page.locator(THINKING_INDICATOR_SEL)
    fallback_indicators = page.locator(FALLBACK_THINKING_SEL)

    # 优先使用精确的定位器
    indicators_to_wait_for = thinking_indicators if thinking_indicators.count() > 0 else fallback_indicators

    if indicators_to_wait_for.count() > 0:
        log("INFO", f"检测到 {indicators_to_wait_for.count()} 个“思考/加载中”指示器，等待其完成...", "AI响应感知")
        try:
            # 遍历所有找到的指示器，等待它们全部消失
            for i in range(indicators_to_wait_for.count()):
                 expect(indicators_to_wait_for.nth(i)).to_be_hidden(timeout=Config.Timeouts.AI_GENERATION_MS)
            log("SUCCESS", f"所有“思考/加载中”指示器已结束。", "AI响应感知")
        except PlaywrightTimeoutError:
            log("WARNING", f"等待部分“思考/加载中”指示器消失超时，可能已出结果。", "AI响应感知")
        except Exception as e:
            # 捕获可能的 StaleElementReferenceError 等错误
             log("WARNING", f"等待指示器消失时遇到错误: {e}，继续执行。", "AI响应感知")


    # 进入稳定观察期，确保所有文本都已输出
    log("INFO", f"进入最终内容稳定观察期...", "AI响应感知")
    last_len = 0
    current_len = page.locator(CHAT_AREA_SEL).evaluate("el => el.innerHTML").__len__()
    page.wait_for_timeout(500) # 初始延迟
    current_len = page.locator(CHAT_AREA_SEL).evaluate("el => el.innerHTML").__len__()

    stability_start_time = time.time()
    while last_len != current_len:
        last_len = current_len
        page.wait_for_timeout(Config.Timeouts.CONTENT_STABILITY_MS)
        current_len = page.locator(CHAT_AREA_SEL).evaluate("el => el.innerHTML").__len__()
        # 增加一个总的稳定观察期超时，防止无限等待
        if time.time() - stability_start_time > Config.Timeouts.WAIT_FOR_CHANGE_MS:
             log("WARNING", "稳定观察期超时，强制认为内容已稳定。")
             break
    
    log("SUCCESS", f"内容已稳定，{agent_name} 已完成完整输出。", "AI响应感知")

def _calculate_bezier_point(t: float, p0: dict, p1: dict, p2: dict) -> dict:
    u = 1 - t
    tt = t * t
    uu = u * u
    x = (uu * p0['x']) + (2 * u * t * p1['x']) + (tt * p2['x'])
    y = (uu * p0['y']) + (2 * u * t * p1['y']) + (tt * p2['y'])
    return {'x': x, 'y': y}

def _move_mouse_human_like(page: Page, target_locator, session: Session):
    log("INFO", "模拟类人鼠标移动...", "鼠标移动")
    viewport_size = page.viewport_size
    start_point = {'x': session.rng.randint(0, viewport_size['width'] if viewport_size else 100), 'y': session.rng.randint(0, viewport_size['height'] if viewport_size else 100)}
    target_box = target_locator.bounding_box()
    if not target_box:
        log("WARNING", "无法获取鼠标移动目标元素的边界框。", "鼠标移动")
        return
    end_point = {'x': target_box['x'] + target_box['width'] / 2, 'y': target_box['y'] + target_box['height'] / 2}
    control_point = {
        'x': (start_point['x'] + end_point['x']) / 2 + session.rng.uniform(-target_box['width'], target_box['width']),
        'y': (start_point['y'] + end_point['y']) / 2 + session.rng.uniform(-target_box['height'], target_box['height'])
    }
    steps = session.rng.randint(25, 40)
    for i in range(steps + 1):
        t = i / steps
        point = _calculate_bezier_point(t, start_point, control_point, end_point)
        page.mouse.move(point['x'], point['y'])
        page.wait_for_timeout(session.rng.uniform(5, 25))

def handle_termination(final_message: str):
    log("SUCCESS", "检测到终止信号。任务完成。", "任务终止")
    log("INFO", f"Agent A 的最终交付内容:\n---\n{final_message}\n---", "任务终止")
    # sys.exit(0)

def send_message_robust(page: Page, message: str, agent_name: str, session: Session):
    if not message or not message.strip():
        log("WARNING", f"尝试向 {agent_name} 发送空消息，已跳过。", "发送消息")
        return
    log("INFO", f"向 {agent_name} 发送消息 (长度: {len(message)})...", "发送消息")
    try:
        session.task_lock = True
        log("INFO", "已获取任务锁。", "L4锁")
        page.bring_to_front()
        
        snapshot_html = page.locator(CHAT_AREA_SEL).evaluate("el => el.innerHTML")
        snapshot_count = page.locator(MESSAGE_ANCHOR_SEL).count()
        log("INFO", f"发送前快照(DOM长度: {len(snapshot_html)}, 锚点数: {snapshot_count})", "发送消息")

        input_box = page.locator(INPUT_SEL)
        send_button_locator = page.locator(SEND_BUTTON_SEL)
        input_box.fill(message)
        log("INFO", "消息内容已填充。", "发送消息")
        page.wait_for_timeout(session.rng.uniform(300, 700))
        _move_mouse_human_like(page, send_button_locator, session)
        send_button_locator.hover()
        page.wait_for_timeout(session.rng.uniform(100, 300))
        send_button_locator.click()

        # 【LOGIC SEPARATION】只等待用户自己的消息上屏，然后立刻返回
        wait_for_initial_change(page, snapshot_html, snapshot_count)
        log("SUCCESS", "用户发送的消息已确认上屏！", "发送消息")

    except Exception as e:
        log("FATAL", f"向 {agent_name} 发送消息时发生严重错误: {e}", "发送消息")
        raise
    finally:
        session.task_lock = False
        log("INFO", "任务锁已释放。", "L4锁")


def get_latest_message_safe(page: Page, agent_name: str) -> str:
    log("INFO", f"为 {agent_name} 执行“次元切割”高权限提取脚本...", "提取消息")
    try:
        js_get_last_message_script = f"""
            () => {{
                const allMessages = document.querySelectorAll('{MESSAGE_ANCHOR_SEL}');
                if (allMessages.length === 0) return '';
                for (let i = allMessages.length - 1; i >= 0; i--) {{
                    const messageContainer = allMessages[i];
                    const clone = messageContainer.cloneNode(true);
                    // 移除页脚、菜单按钮和思考指示器等噪音
                    clone.querySelectorAll('.response-container-footer, [class*="menu-button"], [class*="thoughts"], [class*="generating"], .bard-avatar.thinking').forEach(el => el.remove());
                    // 优先提取 markdown 内容
                    const markdownEl = clone.querySelector('[class*="markdown"]');
                    if (markdownEl && markdownEl.innerText.trim()) {{
                        return markdownEl.innerText.trim();
                    }}
                    // 备用方案：提取清理后的整个容器文本
                    const fullText = clone.innerText;
                    if (fullText && fullText.trim()) {{
                        return fullText.trim();
                    }}
                }}
                return '';
            }}
        """
        message_text = page.evaluate(js_get_last_message_script)

        if not message_text:
            log("WARNING", "高权限提取脚本未能找到任何有效消息内容。", "提取消息")
            last_container = page.locator(MESSAGE_ANCHOR_SEL).last
            if last_container.count() > 0:
                full_text = last_container.inner_text().strip()
                buttons_text = ["复制", "修改回复", "分享和导出", "赞", "踩", "更多选项"]
                for btn_txt in buttons_text:
                    full_text = full_text.replace(btn_txt, "")
                message_text = full_text.strip()


        log("SUCCESS", f"成功从 {agent_name} 提取到消息 (长度: {len(message_text)})", "提取消息")
        return message_text

    except Exception as e:
        log("FATAL", f"无法从 {agent_name} 提取最新消息: {e}", "提取消息")
        raise

# =======================================================================================
# === III. 阶段二：主编排逻辑 (Phase Two: Main Orchestration Logic) ===
# =======================================================================================

def run_orchestrator(page_A: Page, page_B: Page, initial_session: Session):
    log("INFO", "=== 自动化流程正式开始 ===", "主流程")
    session = initial_session
    error_count = 0
    MAX_ERRORS = 3
    while True:
        try:
            page_A.bring_to_front()
            wait_for_page_stability(page_A, "Agent A")
            
            if not page_A.locator(MESSAGE_ANCHOR_SEL).count():
                log("INFO", "场景1: A页面空白，发送启动指令...", "场景处理")
                send_message_robust(page_A, START_CMD_MSG, "Agent A", session)
                snapshot_html = page_A.locator(CHAT_AREA_SEL).evaluate("el => el.innerHTML")
                snapshot_count = page_A.locator(MESSAGE_ANCHOR_SEL).count()
                wait_for_ai_response(page_A, "Agent A", snapshot_html, snapshot_count)
            else:
                log("INFO", "场景2: 检测到已有对话，直接进入协作循环。", "场景处理")


            log("INFO", "状态同步完成。进入主协作循环。", "场景处理")
            while True:
                log("INFO", "--- 开始新一轮协作 ---", "协作循环")

                message_A = get_latest_message_safe(page_A, "Agent A")
                if TERMINATION_PHRASE in message_A:
                    handle_termination(message_A)
                    break
                
                page_B.bring_to_front()
                wait_for_page_stability(page_B, "Agent B")
                # 【LOGIC SEPARATION】步骤1: 发送消息，此函数现在会快速返回
                send_message_robust(page_B, message_A, "Agent B", session)
                # 【LOGIC SEPARATION】步骤2: 专门等待AI的完整回复
                snapshot_html_b = page_B.locator(CHAT_AREA_SEL).evaluate("el => el.innerHTML")
                snapshot_count_b = page_B.locator(MESSAGE_ANCHOR_SEL).count()
                wait_for_ai_response(page_B, "Agent B", snapshot_html_b, snapshot_count_b)

                message_B = get_latest_message_safe(page_B, "Agent B")

                page_A.bring_to_front()
                # 【LOGIC SEPARATION】步骤1: 发送消息
                send_message_robust(page_A, message_B, "Agent A", session)
                # 【LOGIC SEPARATION】步骤2: 等待AI回复
                snapshot_html_a = page_A.locator(CHAT_AREA_SEL).evaluate("el => el.innerHTML")
                snapshot_count_a = page_A.locator(MESSAGE_ANCHOR_SEL).count()
                wait_for_ai_response(page_A, "Agent A", snapshot_html_a, snapshot_count_a)
                
                error_count = 0
                log("SUCCESS", "--- 本轮协作完成 ---", "协作循环")

        except Exception as e:
            error_count += 1
            log("ERROR", f"协作循环出错 (尝试 {error_count}/{MAX_ERRORS}次): {e}", "L4恢复")
            if error_count >= MAX_ERRORS:
                log("CRITICAL", "错误次数达到阈值！触发“理论销毁”...", "L4恢复")
                frustration_wait_s = session.rng.uniform(15, 45)
                log("INFO", f"模拟人类沮丧，暂停 {frustration_wait_s:.1f} 秒...", "L4恢复")
                time.sleep(frustration_wait_s)
                log("CRITICAL", "重置身份！生成新的会话种子...", "L4恢复")
                session = Session(seed=int(time.time()))
                error_count = 0
                log("SUCCESS", "Agent已重生。使用新身份重试任务。", "L4恢复")

# =======================================================================================
# === IV. 阶段三：程序入口与浏览器设置 (Phase Three: Entry Point & Browser Setup) ===
# =======================================================================================
if __name__ == '__main__':
    try:
        if not os.path.exists(Config.EDGE_USER_DATA_PATH):
            raise FileNotFoundError(f"Edge 用户数据目录未找到: {Config.EDGE_USER_DATA_PATH}")
        session = Session(seed=int(time.time()))
        with sync_playwright() as p:
            log("INFO", "正在启动 Edge 浏览器并加载个人配置...", "启动设置")
            context = p.chromium.launch_persistent_context(
                user_data_dir=Config.EDGE_USER_DATA_PATH,
                headless=False,
                channel="msedge",
                slow_mo=50,
                args=['--start-maximized', '--disable-blink-features=AutomationControlled']
            )
            page_A = context.pages[0] if context.pages else context.new_page()
            page_A.goto(Config.GEMINI_URL_A, wait_until="domcontentloaded", timeout=Config.Timeouts.PAGE_LOAD_MS)
            page_B = context.new_page()
            page_B.goto(Config.GEMINI_URL_B, wait_until="domcontentloaded", timeout=Config.Timeouts.PAGE_LOAD_MS)
            log("SUCCESS", "双Agent页面已加载。", "启动设置")
            wait_for_page_stability(page_A, "Agent A")
            wait_for_page_stability(page_B, "Agent B")
            page_A.bring_to_front()
            log("WARNING", f"您有 {Config.Timeouts.MANUAL_SETUP_SEC} 秒进行手动设置。", "手动设置")
            log("WARNING", "请在 Agent A (第一个标签页) 中输入您的初始任务。", "手动设置")
            for i in range(Config.Timeouts.MANUAL_SETUP_SEC, 0, -10):
                log("INFO", f"剩余时间: {i} 秒...", "手动设置")
                time.sleep(10)
            run_orchestrator(page_A=page_A, page_B=page_B, initial_session=session)
    except FileNotFoundError as e:
        log("FATAL", str(e), "启动错误")
    except Exception as e:
        log("FATAL", f"脚本因未知错误而终止: {e}", "运行时错误")
    finally:
        log("INFO", "脚本执行结束。", "关闭")
        input("按回车键关闭浏览器...")

