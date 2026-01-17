const { chromium } = require('playwright');
const http = require('http');

let browser; // Global browser instance

async function startBrowser() {
    // Launch browser once and reuse it across requests
    browser = await chromium.launch();
    console.log('Playwright browser launched.');
}

async function handleRequest(req, res) {
    if (req.method === 'POST' && req.url === '/execute') {
        let body = '';
        req.on('data', chunk => {
            body += chunk.toString();
        });
        req.on('end', async () => {
            let context = null;
            let page = null;
            try {
                const { action, args } = JSON.parse(body);
                console.log('Received action: %s with args:', action, args);

                if (!browser) {
                    await startBrowser();
                }

                let result = { success: false, error: "Unknown action" };

                switch (action) {
                    case 'execute_script':
                        // args.script should be an array of {action: "...", args: {...}}
                        if (!Array.isArray(args.script)) {
                            throw new Error("Script must be an array of actions.");
                        }
                        
                        // Create a fresh context and page for the script execution
                        context = args.storageState ?
                            await browser.newContext({ storageState: args.storageState }) :
                            await browser.newContext();
                        page = await context.newPage();

                        let scriptResults = [];
                        for (const scriptAction of args.script) {
                            const currentAction = scriptAction.action;
                            const currentArgs = scriptAction.args;
                            let stepResult;

                            try {
                                switch (currentAction) {
                                    case 'goto':
                                        await page.goto(currentArgs.url);
                                        stepResult = { success: true, action: 'goto', url: currentArgs.url, title: await page.title(), content: await page.content() };
                                        break;
                                    case 'screenshot':
                                        const screenshotPath = currentArgs.path;
                                        await page.screenshot({ path: screenshotPath });
                                        stepResult = { success: true, action: 'screenshot', path: screenshotPath };
                                        break;
                                    case 'get_element_bounding_box':
                                        const bbox = await page.$eval(currentArgs.selector, el => {
                                            const { x, y, width, height } = el.getBoundingClientRect();
                                            return { x, y, width, height };
                                        }).catch(() => null); // Return null if selector not found
                                        stepResult = { success: true, action: 'get_element_bounding_box', selector: currentArgs.selector, bbox: bbox };
                                        break;
                                    case 'click_element':
                                        await page.click(currentArgs.selector);
                                        stepResult = { success: true, action: 'click_element', selector: currentArgs.selector };
                                        break;
                                    case 'wait_for_selector':
                                        await page.waitForSelector(currentArgs.selector, { timeout: currentArgs.timeout || 10000 });
                                        stepResult = { success: true, action: 'wait_for_selector', selector: currentArgs.selector };
                                        break;
                                    case 'evaluate':
                                        const evalResult = await page.evaluate(currentArgs.script);
                                        stepResult = { success: true, action: 'evaluate', script: currentArgs.script, result: evalResult };
                                        break;
                                    case 'get_content': // Generic content getter
                                        stepResult = { success: true, action: 'get_content', content: await page.content() };
                                        break;
                                    // Add other Playwright actions as needed
                                    default:
                                        throw new Error(`Unknown script action: ${currentAction}`);
                                }
                            } catch (stepError) {
                                console.error('Error during script step \'%s\':', currentAction, stepError);
                                stepResult = { success: false, action: currentAction, error: stepError.message };
                                // Decide whether to continue or break on error
                                throw stepError; // Break the script execution on first error
                            }
                            scriptResults.push(stepResult);
                        }
                        result = { success: true, script_execution_results: scriptResults };
                        break;
                    default:
                        throw new Error(`Unknown action: ${action}. Use 'execute_script' for complex flows.`);
                }
                
                res.writeHead(200, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify(result));

            } catch (error) {
                console.error('Error handling request:', error);
                res.writeHead(500, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ success: false, error: error.message }));
            } finally {
                // Ensure context and page are closed after each request
                if (page) await page.close();
                if (context) await context.close();
            }
        });
    } else {
        res.writeHead(404, { 'Content-Type': 'text/plain' });
        res.end('Not Found');
    }
}

async function main() {
    await startBrowser(); // Launch browser once on server start

    const server = http.createServer(handleRequest);
    const PORT = process.env.PORT || 8080;
    server.listen(PORT, () => {
        console.log('MCP server listening on port %d', PORT);
    });
}

main();
