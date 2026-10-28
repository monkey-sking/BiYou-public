"""
用 BiYou 的浏览器批量删除 Gemini 对话
前提：BiYou 的浏览器已启动 (CDP 端口来自 config.yaml，profile 默认 ~/.biyou-workflow-browser-data)
"""
import asyncio
from playwright.async_api import async_playwright

from core.utils.browser_config import DEFAULT_BROWSER_DEBUG_PORT, get_browser_debug_port
from core.utils.config import load_config


async def main():
    debug_port = DEFAULT_BROWSER_DEBUG_PORT
    try:
        debug_port = get_browser_debug_port(load_config())
    except Exception:
        pass

    async with async_playwright() as p:
        page = None
        try:
            browser = await p.chromium.connect_over_cdp(f'http://localhost:{debug_port}')
            print(f'✅ 已连接 BiYou 浏览器 (CDP {debug_port})')
        except Exception as e:
            print(f'❌ 无法连接: {e}')
            return
        
        context = browser.contexts[0]
        # Always use a task-owned page so shared workflow tabs/browser stay intact.
        page = await context.new_page()
        
        try:
            await page.goto('https://gemini.google.com/app')
            await page.wait_for_timeout(3000)

            # 先数有多少对话
            count = await page.locator('a.conversation').count()
            if count == 0:
                print('✅ 没有对话')
                return

            print(f'🗑️  找到 {count} 个对话，开始批量删除...')

            # 用 JS 直接完成所有操作
            deleted = await page.evaluate("""
        async () => {
            function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
            let deleted = 0;
            
            for (let i = 0; i < 200; i++) {
                const items = document.querySelectorAll('a.conversation');
                if (items.length === 0) break;
                
                const first = items[0];
                
                // 模拟右键点击
                first.dispatchEvent(new MouseEvent('contextmenu', {
                    bubbles: true, cancelable: true, 
                    clientX: first.getBoundingClientRect().x + 100,
                    clientY: first.getBoundingClientRect().y + 10,
                    button: 2
                }));
                await sleep(1000);
                
                // 找 Delete/删除 菜单项
                const allItems = document.querySelectorAll('[role="menuitem"], .mat-mdc-menu-item, .mdc-list-item');
                let deleteBtn = null;
                for (const item of allItems) {
                    const text = item.textContent.trim();
                    if (text === 'Delete' || text === '删除' || text.includes('Delete') || text.includes('删除')) {
                        deleteBtn = item;
                        break;
                    }
                }
                
                if (!deleteBtn) {
                    // 也找 overlay 里的
                    const overlayItems = document.querySelectorAll('.cdk-overlay-container [role="menuitem"], .cdk-overlay-container button');
                    for (const item of overlayItems) {
                        const text = item.textContent.trim();
                        if (text === 'Delete' || text === '删除' || text.includes('Delete') || text.includes('删除')) {
                            deleteBtn = item;
                            break;
                        }
                    }
                }
                
                if (!deleteBtn) {
                    console.log('Menu items found:', document.querySelectorAll('[role="menuitem"]').length);
                    console.log('CDK overlay items:', document.querySelectorAll('.cdk-overlay-container *').length);
                    // dump first few elements in overlay
                    const overlayEls = document.querySelectorAll('.cdk-overlay-container *');
                    for (let j = 0; j < Math.min(10, overlayEls.length); j++) {
                        console.log('  overlay el:', overlayEls[j].tagName, overlayEls[j].className, overlayEls[j].textContent.substring(0, 50));
                    }
                    break;
                }
                
                deleteBtn.click();
                await sleep(1000);
                
                // 确认弹窗
                const buttons = document.querySelectorAll('mat-dialog-actions button, .mat-mdc-dialog-actions button, .cdk-overlay-container button');
                let confirmBtn = null;
                for (const btn of buttons) {
                    const text = btn.textContent.trim();
                    if (text === 'Delete' || text === '删除') {
                        confirmBtn = btn;
                    }
                }
                
                if (confirmBtn) {
                    confirmBtn.click();
                    await sleep(1500);
                    deleted++;
                }
            }
            
            return deleted;
        }
        """)

            print(f'\n🎉 完成！共删除 {deleted} 个对话')

            # 检查控制台输出 (debug)
            page.on('console', lambda msg: print(f'  [console] {msg.text}'))
            await page.wait_for_timeout(500)
        finally:
            if page:
                await page.close()

asyncio.run(main())
