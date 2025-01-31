# webrover_browser.py
import asyncio
import platform
from pathlib import Path
from typing import Optional, Tuple
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
import re
import os
import aiohttp
import socket

class WebRoverBrowser:
    def __init__(self, 
                 user_data_dir: Optional[str] = None,
                 headless: bool = False,
                 proxy: Optional[str] = None):
        base_dir = self._default_user_dir()
        # Initially just store the base Chrome directory
        self.base_user_dir = base_dir
        # Will be set after profile selection
        self.user_data_dir = None
        self.headless = headless
        self.proxy = proxy
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._playwright = None

    def _default_user_dir(self) -> str:
        """Get platform-specific default user data directory"""
        system = platform.system()
        if system == "Windows":
            return str(Path.home() / "AppData/Local/Google/Chrome/User Data")
        elif system == "Darwin":
            return str(Path.home() / "Library/Application Support/Google/Chrome")
        else:  # Linux
            return str(Path.home() / ".config/google-chrome")

    async def connect_to_chrome(self, 
                              timeout: float = 30,
                              retries: int = 3) -> Tuple[async_playwright, Browser, BrowserContext]:
        """Connect to existing Chrome instance with retry logic"""
        self._playwright = await async_playwright().start()
        
        print("Starting Chrome with remote debugging...")
        chrome_process = await self.launch_chrome_with_remote_debugging()
        
        await asyncio.sleep(3)  # Wait for Chrome to start
        
        print("Attempting to connect to Chrome...")
        for attempt in range(retries):
            try:
                # Connect to the existing Chrome instance
                ws_endpoint = None
                # Get the WebSocket endpoint from Chrome's debugging API
                async with aiohttp.ClientSession() as session:
                    # Then get the browser websocket URL
                    async with session.get("http://127.0.0.1:9222/json/version") as response:
                        data = await response.json()
                        ws_endpoint = data.get('webSocketDebuggerUrl')
                
                if not ws_endpoint:
                    raise RuntimeError("Could not get WebSocket debugger URL")
                
                print(f"Connecting to WebSocket endpoint: {ws_endpoint}")
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    ws_endpoint
                )


                print("Connected to browser:", self._browser)
                
                # Use the first available context instead of creating a new one
                contexts = self._browser.contexts
                if not contexts:
                    raise RuntimeError("No browser contexts available after connection")
                self._context = contexts[0]
                print("Context: ", self._context)
                
                print("Successfully connected to Chrome")
                return self._browser, self._context
            
            except Exception as e:
                print(f"Connection attempt {attempt + 1} failed: {str(e)}")
                if attempt == retries - 1:
                    raise RuntimeError(f"Failed to connect to Chrome after {retries} attempts: {str(e)}")
                await asyncio.sleep(2 ** attempt)

    async def launch_chrome_with_remote_debugging(self):
        """Launch Chrome with remote debugging port"""
        # First launch Chrome normally to let user select profile
        chrome_path = {
            "Windows": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            "Darwin": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "Linux": "/usr/bin/google-chrome"
        }.get(platform.system())

        cmd = [
            chrome_path,
            f"--remote-debugging-port=9222",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized"
        ]

        if self.headless:
            cmd.append("--headless=new")

        try:
            print("Launching Chrome with command:", " ".join(cmd))
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            return process
            
        except Exception as e:
            print(f"Error launching Chrome: {e}")
            if process:
                stderr = await process.stderr.read()
                print(f"Chrome stderr output: {stderr.decode()}")
            raise RuntimeError(f"Failed to launch Chrome: {str(e)}")

    async def create_context(self, 
                           viewport: dict = {"width": 1280, "height": 720},
                           user_agent: str = None) -> BrowserContext:
        """Create optimized browser context with human-like settings"""
        if not self._browser:
            raise RuntimeError("Browser not connected. Call connect_to_chrome first.")

        # Return existing context if it exists
        if self._context:
            return self._context

        # Create new context with optimized settings
        self._context = await self._browser.new_context(
            viewport=viewport,
            user_agent=user_agent or self._modern_user_agent(),
            locale="en-US",
            timezone_id="America/New_York",
            permissions=["geolocation"],
            geolocation={"latitude": 40.7128, "longitude": -74.0060},  # NYC
            http_credentials=None,
            proxy=self._proxy_settings(),
            color_scheme="light",
        )

        await self._add_anti_detection()
        await self._configure_network()
        return self._context

    async def _add_anti_detection(self):
        """Inject JavaScript to mask automation"""
        await self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US'] });
            window.chrome = { runtime: {} };
        """)

    async def _configure_network(self):
        """Block unnecessary resources for faster loading"""
        await self._context.route("**/*.{png,jpg,jpeg,webp}", lambda route: route.abort())
        await self._context.route("**/*.css", lambda route: route.abort())
        await self._context.route(re.compile(r"(analytics|tracking|beacon)"), lambda route: route.abort())

    def _modern_user_agent(self) -> str:
        """Generate current Chrome user agent string"""
        versions = {
            "Windows": "122.0.0.0",
            "Darwin": "122.0.0.0",
            "Linux": "122.0.0.0"
        }
        system = platform.system()
        return f"Mozilla/5.0 ({self._os_info()}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{versions[system]} Safari/537.36"

    def _os_info(self) -> str:
        """Get platform-specific OS info for user agent"""
        if platform.system() == "Windows":
            return "Windows NT 10.0; Win64; x64"
        elif platform.system() == "Darwin":
            return "Macintosh; Intel Mac OS X 10_15_7"
        else:
            return "X11; Linux x86_64"

    def _proxy_settings(self) -> Optional[dict]:
        """Parse proxy configuration"""
        if not self.proxy:
            return None
        return {
            "server": self.proxy,
            "username": os.getenv("PROXY_USER"),
            "password": os.getenv("PROXY_PASS")
        }

    async def close(self):
        """Cleanup resources"""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()


    async def get_chrome_paths():
        async with async_playwright() as p:
            # Launch a headless Chrome browser
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            # Navigate to chrome://version/
            await page.goto('chrome://version/')

            # Wait for the page to load (ensure the content is available)
            await page.wait_for_selector('body')

            # Extract the Executable Path and Profile Path
            executable_path = await page.evaluate('''() => {
                const labels = Array.from(document.querySelectorAll('body > div > div'));
                const executablePathLabel = labels.find(el => el.textContent.includes('Executable Path'));
                return executablePathLabel ? executablePathLabel.nextElementSibling.textContent : null;
            }''')

            profile_path = await page.evaluate('''() => {
                const labels = Array.from(document.querySelectorAll('body > div > div'));
                const profilePathLabel = labels.find(el => el.textContent.includes('Profile Path'));
                return profilePathLabel ? profilePathLabel.nextElementSibling.textContent : null;
            }''')

            # Print the extracted paths
            print(f"Executable Path: {executable_path}")
            print(f"Profile Path: {profile_path}")

            # Close the browser
            await browser.close()

            return executable_path, profile_path