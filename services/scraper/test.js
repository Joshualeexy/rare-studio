const { launchStealthBrowser } = require('./lib/browser');

async function test() {
    console.log('🚀 Testing launchStealthBrowser...');
    const browser = await launchStealthBrowser({
        headless: true,
        userDataDir: './test_profile'
    });
    console.log('✅ Browser launched successfully!');
    
    const page = await browser.newPage();
    
    console.log('🌐 Navigating to https://tiktok.com...');
    await page.goto('https://tiktok.com/@manutd', { waitUntil: 'domcontentloaded' });
    console.log('🎉 Navigation successful! Page title:', await page.title());
    
    await browser.close();
    console.log('🔒 Browser closed. Test passed!');
}

test().catch(err => {
    console.error('❌ Test failed:', err);
    process.exit(1);
});
