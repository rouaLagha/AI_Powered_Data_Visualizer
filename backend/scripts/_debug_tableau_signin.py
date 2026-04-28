import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://public.tableau.com/app/discover", timeout=120000)
        await page.wait_for_timeout(6000)

        selectors = [
            "a:has-text('Sign In')",
            "button:has-text('Sign In')",
            "a:has-text('Log In')",
            "button:has-text('Log In')",
            "[data-testid*='signin']",
            "[aria-label*='sign' i]",
        ]

        print("URL:", page.url)
        print("TITLE:", await page.title())
        for selector in selectors:
            count = await page.locator(selector).count()
            print(selector, "=>", count)
            if count > 0:
                first = page.locator(selector).first
                text = (await first.inner_text()).strip()
                href = await first.get_attribute("href")
                print("  first text=", text)
                print("  first href=", href)

        print("\nClicking Sign In...")
        await page.locator("button:has-text('Sign In')").first.click()
        await page.wait_for_timeout(8000)

        print("POST-CLICK URL:", page.url)
        print("POST-CLICK TITLE:", await page.title())
        print("POST-CLICK FRAME COUNT:", len(page.frames))
        for i, frame in enumerate(page.frames):
            print(" FRAME", i, "URL", frame.url)

        # Try locating inputs on main page.
        print("MAIN email count:", await page.locator("input[type='email'], input[name='email'], input[autocomplete='username']").count())
        print("MAIN pwd count:", await page.locator("input[type='password'], input[name='password']").count())

        # Try locating inputs in all frames.
        for i, frame in enumerate(page.frames):
            try:
                email_count = await frame.locator("input[type='email'], input[name='email'], input[autocomplete='username']").count()
                pwd_count = await frame.locator("input[type='password'], input[name='password']").count()
                print(" FRAME", i, "email=", email_count, "pwd=", pwd_count)
            except Exception as exc:
                print(" FRAME", i, "locator error:", type(exc).__name__, str(exc))

        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
