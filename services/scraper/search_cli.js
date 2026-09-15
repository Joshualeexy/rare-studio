const { launchStealthBrowser } = require('./lib/browser');
const path = require('path');
require('dotenv').config();

async function searchGoogle(query, limit = 5) {
    const browser = await launchStealthBrowser({
        headless: true,
        userDataDir: path.resolve(__dirname, 'google_profile'),
        capsolverApiKey: process.env.CAPSOLVER_API_KEY || ''
    });

    const page = await browser.newPage();
    const searchUrl = `https://www.google.com/search?q=${encodeURIComponent(query)}&hl=en&num=10`;

    try {
        await page.goto(searchUrl, { waitUntil: 'domcontentloaded', timeout: 25000 });

        // Check for GDPR consent
        try {
            const consentSelectors = ['button:has-text("Accept all")', 'button:has-text("I agree")', '#L2AGLb'];
            for (const sel of consentSelectors) {
                const btn = await page.$(sel);
                if (btn) {
                    await btn.click().catch(() => null);
                    await page.waitForTimeout(500);
                    break;
                }
            }
        } catch (_) {}

        // If on sorry/index, click anchor to trigger CapSolver / Google pass-through
        if (page.url().includes('sorry/index')) {
            console.log('[Scraper] Verifying access checkpoint...');
            await page.waitForTimeout(2000);
            const anchorFrame = page.frames().find(f => f.url().includes('/recaptcha/enterprise/anchor') || f.url().includes('/recaptcha/api2/anchor'));
            if (anchorFrame) {
                const anchor = await anchorFrame.$('#recaptcha-anchor');
                if (anchor) {
                    await anchor.click().catch(() => null);
                }
            }
            // Wait for navigation back to search
            await page.waitForURL(url => !url.href.includes('sorry/index'), { timeout: 15000 }).catch(() => null);
        }

        // Wait for results
        await page.waitForSelector('#search, .g, div[data-async-context]', { timeout: 8000 }).catch(() => null);

        // Extract search intelligence
        const results = await page.evaluate((maxResults) => {
            let answerBox = '';
            const answerEl = document.querySelector('.hgKElc, [data-attrid="wa:/description"], .kp-header, .Z0LcW, .LGOjdf');
            if (answerEl) {
                answerBox = answerEl.innerText.trim();
            }

            const organic = [];
            const cards = document.querySelectorAll('.g, div[data-hveid].MjjYud');

            for (const card of cards) {
                const titleEl = card.querySelector('h3');
                const linkEl = card.querySelector('a[href^="http"]');
                const snippetEl = card.querySelector('.VwiC3b, .yXK7lf, .MUxGbd, .bNe31b');

                const title = titleEl ? titleEl.innerText.trim() : '';
                const link = linkEl ? linkEl.getAttribute('href') : '';
                const snippet = snippetEl ? snippetEl.innerText.trim() : '';

                if (title && snippet && !link.includes('google.com/search')) {
                    if (!organic.some(r => r.url === link)) {
                        organic.push({ title, snippet, url: link });
                    }
                }
                if (organic.length >= maxResults) break;
            }

            const paa = [];
            const paaEls = document.querySelectorAll('.related-question-pair, div[data-q]');
            for (const el of paaEls) {
                const qText = el.innerText ? el.innerText.split('\n')[0].trim() : '';
                if (qText && qText.endsWith('?')) {
                    paa.push(qText);
                }
                if (paa.length >= 4) break;
            }

            return {
                answer_box: answerBox,
                results: organic,
                people_also_ask: paa
            };
        }, limit);

        return {
            status: 'ok',
            query,
            answer_box: results.answer_box,
            results: results.results,
            people_also_ask: results.people_also_ask
        };

    } finally {
        await browser.close().catch(() => null);
    }
}

if (require.main === module) {
    const q = process.argv.slice(2).join(' ') || 'Pupil Dilation Lie Detection Sign';
    searchGoogle(q, 4)
        .then(res => console.log(JSON.stringify(res, null, 2)))
        .catch(err => {
            console.error(JSON.stringify({ status: 'error', error: err.message, results: [] }));
            process.exit(1);
        });
}

module.exports = { searchGoogle };
