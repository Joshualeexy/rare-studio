const path = require('path');
require('dotenv').config({ path: path.resolve(__dirname, '.env') });
require('dotenv').config({ path: path.resolve(__dirname, '../../.env') });
require('dotenv').config();

const express = require('express');
const cors = require('cors');
const { launchStealthBrowser } = require('./lib/browser');

const app = express();
const PORT = process.env.SCRAPER_PORT || 4050;

app.use(cors());
app.use(express.json());

// Global persistent browser pool & default page
let persistentBrowser = null;
let defaultPage = null;
let isInitializing = false;

// Curated 21 Trusted Archival Domains (100% Watermark-Free & Authentic)
const TRUSTED_DOMAINS = [
    'rarehistoricalphotos.com',
    'historydefined.net',
    'allthatsinteresting.com',
    'interestingengineering.com',
    'historycolored.com',
    '121clicks.com',
    'boredpanda.com',
    'buzzfeed.com',
    'archiveproject.com',
    'oldphotogallery.com',
    'illustratedpast.com',
    'thevintagenews.com',
    'monovisions.com',
    'livescience.com',
    'flashbak.com',
    'messynessychic.com',
    'vintag.es',
    'loc.gov',
    'smithsonianmag.com',
    'archives.gov',
    'images.nasa.gov'
];

/**
 * Initialize or retrieve the persistent stealth browser context.
 */
async function getBrowser() {
    if (persistentBrowser) return persistentBrowser;
    if (isInitializing) {
        while (isInitializing) {
            await new Promise(r => setTimeout(r, 200));
        }
        return persistentBrowser;
    }

    isInitializing = true;
    try {
        console.log('[Scraper] Initializing persistent stealth browser pool...');
        persistentBrowser = await launchStealthBrowser({
            headless: true,
            userDataDir: path.resolve(__dirname, 'browser_profile'),
            capsolverApiKey: process.env.CAPSOLVER_API_KEY || ''
        });

        defaultPage = await persistentBrowser.newPage();
        await defaultPage.goto('about:blank');
        console.log('[Scraper] Persistent stealth browser pool is ready!');
        return persistentBrowser;
    } catch (err) {
        console.error('[Scraper] Failed to launch browser pool:', err);
        throw err;
    } finally {
        isInitializing = false;
    }
}

// Memory tracking of recently harvested articles to ensure fresh rotation across runs
const recentlyHarvestedUrls = new Set();

/**
 * Universal photo & story extractor for editorial photo collections.
 * Handles lazy-loading, "Load More" buttons, and CDN de-downsampling across
 * BuzzFeed, Bored Panda, Rare Historical Photos, All That's Interesting, etc.
 */
async function extractArticlePhotos(page, articleUrl) {
    console.log(`[Scraper] Harvesting curated article items from: "${articleUrl}"`);
    await page.goto(articleUrl, { waitUntil: 'domcontentloaded', timeout: 25000 });
    await page.waitForTimeout(2000);

    // Multi-step incremental scroll loop to trigger viewport intersection observers & lazy loaders
    for (let step = 0; step < 5; step++) {
        await page.evaluate(() => window.scrollBy(0, 1200)).catch(() => null);
        await page.waitForTimeout(500);
    }

    // Attempt clicking "Load More" or "View More" if present (Bored Panda, listicles)
    try {
        const loadMore = await page.$('.load-more, .open-list-next, button:has-text("Load More"), button:has-text("View More")');
        if (loadMore) {
            await loadMore.click().catch(() => null);
            await page.waitForTimeout(1200);
            await page.evaluate(() => window.scrollBy(0, 1000)).catch(() => null);
            await page.waitForTimeout(500);
        }
    } catch (_) {}

    const items = await page.evaluate(() => {
        const list = [];
        const seenSrcs = new Set();

        function cleanImageUrl(src) {
            if (!src || !src.startsWith('http')) return null;
            let clean = src.trim();

            // Strip BuzzFeed resize / crop parameters to get original full-res photo
            if (clean.includes('buzzfeed.com') || clean.includes('buzzfeedstatic.com')) {
                clean = clean.split('?')[0];
            }
            // De-downsample Bored Panda thumbnails (__700.jpg -> original .jpg / .png)
            if (clean.includes('boredpanda.com')) {
                clean = clean.replace(/__700\.(jpg|jpeg|png|webp)/i, '.$1');
            }

            // Exclude non-content icons, tracking pixels, logos, avatars, thumbnails of related posts, and social badges
            const lower = clean.toLowerCase();
            if (lower.includes('logo') || lower.includes('avatar') || lower.includes('icon') ||
                lower.includes('banner') || lower.includes('pixel') || lower.includes('spacer') ||
                lower.includes('badge') || lower.includes('button') || lower.includes('/thumb/') ||
                lower.includes('flipboard') || lower.includes('advertisement') ||
                lower.includes('alamy') || lower.includes('getty') || lower.includes('shutterstock') ||
                lower.includes('istock') || lower.includes('stockphoto') ||
                lower.endsWith('.svg') || lower.endsWith('.gif')) {
                return null;
            }
            return clean;
        }

        // ── Step A: Locate Main Article Root Container ──
        const articleRoot = document.querySelector('article, .post-content, .entry-content, .article-content, [itemprop="articleBody"], main') || document.body;

        // Function to check if an element is inside header, nav, footer, sidebar, or related posts
        function isJunkElement(el) {
            if (!el) return true;
            return !!el.closest('header, nav, footer, .site-header, .site-footer, .related, .related-posts, .recommended, .sidebar, .widget, .comments, .author-box, .advertisement, [class*="menu"], [class*="nav"], [class*="social"], [class*="sharing"], [class*="share"]');
        }

        function cleanTitle(raw) {
            if (!raw) return '';
            let t = raw.replace(/^\d+[\.\s\-:]*/, '').trim();
            // Reject navigation strings, sharing buttons, related post teasers
            const lower = t.toLowerCase();
            if (t.includes('|') || lower.includes('today in history') || lower.includes('curiosities') ||
                lower.includes('sign in') || lower.includes('subscribe') || lower.includes('comment') ||
                lower.includes('flipboard') || lower.includes('share') || lower.includes('follow us') ||
                lower.includes('read next') || lower.includes('related') || lower.length < 3) {
                return '';
            }
            return t;
        }

        // ── Step B: Check BuzzFeed Subbuzz & Bored Panda Open-List Items First ──
        const listContainers = Array.from(articleRoot.querySelectorAll('.subbuzz, .subbuzz__content, .open-list-item, .list-item'));
        for (const item of listContainers) {
            if (isJunkElement(item)) continue;

            const titleEl = item.querySelector('.subbuzz__title, .subbuzz__title-text, h2, h3, .open-list-header');
            const title = cleanTitle(titleEl ? titleEl.innerText : '');

            const imgEl = item.querySelector('img');
            if (!imgEl) continue;

            const rawSrc = imgEl.getAttribute('data-src') || imgEl.getAttribute('src');
            const cleanSrc = cleanImageUrl(rawSrc);
            if (!cleanSrc || seenSrcs.has(cleanSrc)) continue;

            const descEl = item.querySelector('.subbuzz__description, .open-list-description, p');
            const backstory = descEl ? descEl.innerText.trim() : (imgEl.getAttribute('alt') || title);

            seenSrcs.add(cleanSrc);
            list.push({
                title: title || cleanTitle(imgEl.getAttribute('alt')) || 'Archival Photograph',
                raw_header: title || backstory.substring(0, 60),
                image_url: cleanSrc,
                backstory: backstory || title || 'Archival photograph from historical records.',
                source_url: window.location.href
            });
        }

        // ── Step C: Universal Article Image Extraction ──
        const articleImages = Array.from(articleRoot.querySelectorAll('img'));
        for (const img of articleImages) {
            if (isJunkElement(img)) continue;

            const rawSrc = img.getAttribute('data-src') || img.getAttribute('src');
            const cleanSrc = cleanImageUrl(rawSrc);
            if (!cleanSrc || seenSrcs.has(cleanSrc)) continue;

            let caption = '';
            let backstory = '';

            const fig = img.closest('figure, .wp-block-image, .image-scale-container, .image-container');
            if (fig) {
                const figCap = fig.querySelector('figcaption, .wp-caption-text');
                if (figCap && figCap.innerText.trim().length > 3) {
                    caption = figCap.innerText.trim();
                }
            }

            if (!caption) {
                caption = (img.getAttribute('alt') || img.getAttribute('title') || '').trim();
            }

            let heading = '';
            let prev = img.previousElementSibling || (img.parentElement ? img.parentElement.previousElementSibling : null);
            for (let d = 0; d < 3 && prev; d++) {
                if (/^H[1-6]$/.test(prev.tagName) && prev.innerText.trim().length > 3) {
                    heading = cleanTitle(prev.innerText);
                    break;
                }
                prev = prev.previousElementSibling;
            }

            let next = img.nextElementSibling || (img.parentElement ? img.parentElement.nextElementSibling : null);
            for (let d = 0; d < 3 && next; d++) {
                if (next.tagName === 'P' && next.innerText.trim().length > 20) {
                    backstory += ' ' + next.innerText.trim();
                }
                next = next.nextElementSibling;
            }

            const chosenTitle = cleanTitle(heading || caption) || 'Historical Photograph';
            const finalBackstory = (backstory.trim() || caption || chosenTitle || 'Archival photograph from historical records.');

            const w = parseInt(img.getAttribute('width') || img.naturalWidth || '0', 10);
            const h = parseInt(img.getAttribute('height') || img.naturalHeight || '0', 10);
            if (w > 0 && h > 0 && (w < 250 || h < 200)) continue;

            seenSrcs.add(cleanSrc);
            list.push({
                title: chosenTitle,
                raw_header: heading || caption || chosenTitle,
                image_url: cleanSrc,
                backstory: finalBackstory,
                source_url: window.location.href
            });
        }

        return list;
    });

    return items;
}

// ── Health Check ─────────────────────────────────────────────────────────────
app.get('/health', (req, res) => {
    res.json({
        status: 'ok',
        browserReady: !!persistentBrowser,
        port: PORT
    });
});

// ── Scrape Specific Curated Article Page ──────────────────────────────────────
app.get('/api/scrape_article', async (req, res) => {
    const articleUrl = req.query.url;
    if (!articleUrl) {
        return res.status(400).json({ error: 'Missing required parameter "url"' });
    }

    let page = null;
    try {
        const browser = await getBrowser();
        page = await browser.newPage();

        const items = await extractArticlePhotos(page, articleUrl);
        console.log(`[Scraper] Successfully extracted ${items.length} curated photo items from "${articleUrl}"`);

        res.json({
            status: 'ok',
            url: articleUrl,
            items: items
        });

    } catch (err) {
        console.error(`[Scraper] Article scraping failed for "${articleUrl}":`, err.message);
        res.status(500).json({ status: 'error', url: articleUrl, error: err.message, items: [] });
    } finally {
        if (page) await page.close().catch(() => null);
    }
});

// ── Discover & Scrape Top Curated Article for Topic ─────────────────────────
app.get('/api/discover_article', async (req, res) => {
    const topic = req.query.topic || 'rare historical photos';
    const limit = parseInt(req.query.limit || '5', 10);

    let page = null;
    try {
        const browser = await getBrowser();

        // ─── TIER 1: Curated Vault of Verified Photo-Collection Article URLs ────────
        // Direct links containing 20–100+ historical photographs with rich backstories.
        const CURATED_PHOTO_ARTICLES = [
            // ── BuzzFeed: Curated Viral Historical Photo Galleries ──
            { url: 'https://www.buzzfeed.com/abbyzinman/rare-historical-photos', title: '31 Rare Historical Photos', tags: 'rare historical photos history unseen vintage buzzfeed' },
            { url: 'https://www.buzzfeed.com/daves4/fascinating-and-rare-pictures-fs', title: 'Fascinating and Rare Historical Photos', tags: 'fascinating rare pictures historical photos old buzzfeed' },
            { url: 'https://www.buzzfeed.com/daves4/best-rare-historical-photos-pictures-fs', title: 'Best Rare Historical Photos', tags: 'best rare historical photos pictures iconic vintage buzzfeed' },
            { url: 'https://www.buzzfeed.com/daves4/historical-photos-never-seen-before', title: 'Historical Photos You Have Never Seen Before', tags: 'never seen before rare historical photos unseen buzzfeed' },
            { url: 'https://www.buzzfeed.com/mikespohr/rare-historical-photos', title: 'Rare Historical Photos That Offer A Glimpse Into The Past', tags: 'rare historical photos past glimpse history buzzfeed' },

            // ── Bored Panda: Deep Editorial Old Photo Compilations ──
            { url: 'https://www.boredpanda.com/interesting-old-photos/', title: '50 Interesting Old Photos', tags: 'interesting old photos rare vintage historical boredpanda' },
            { url: 'https://www.boredpanda.com/history-cord-interesting-photos/', title: '83 Fascinating And Emotional Historical Photos', tags: 'fascinating emotional historical photos forgotten past boredpanda' },
            { url: 'https://www.boredpanda.com/rare-historical-photos/', title: 'Rare Historical Photos From The Past', tags: 'rare historical photos past history perspective boredpanda' },
            { url: 'https://www.boredpanda.com/unusual-historical-photos/', title: 'Unusual Historical Photos You Probably Haven\'t Seen', tags: 'unusual strange bizarre historical photos weird boredpanda' },
            { url: 'https://www.boredpanda.com/historical-photos-reddit/', title: 'Historical Photos That Show A Different Side Of History', tags: 'different side history historical photos rare boredpanda' },

            // ── Rare Historical Photos: Definitive Archival Vaults ──
            { url: 'https://rarehistoricalphotos.com/100-influential-historical-pictures-all-time/', title: '100 Influential Historical Pictures of All Time', tags: '100 influential historical pictures famous iconic changed world' },
            { url: 'https://rarehistoricalphotos.com/vintage-bizarre-historical-photos/', title: 'Bizarre and Unique Photos From History, 1910–1960', tags: 'bizarre unique vintage 1910 1960 old weird unusual' },
            { url: 'https://rarehistoricalphotos.com/chernobyl-disaster-photos/', title: 'Chernobyl Disaster in Rare Pictures', tags: 'chernobyl disaster nuclear radiation soviet ukraine pripyat' },
            { url: 'https://rarehistoricalphotos.com/hiroshima-nagasaki-bombing-pictures/', title: 'Hiroshima and Nagasaki Bombing Photos', tags: 'hiroshima nagasaki bombing atomic nuclear war japan 1945' },
            { url: 'https://rarehistoricalphotos.com/titanic-survivors-pictures/', title: 'Real Titanic Photos', tags: 'titanic ship sinking ocean iceberg maritime 1912' },
            { url: 'https://rarehistoricalphotos.com/world-war-2-photos/', title: 'World War 2 in Pictures', tags: 'ww2 world war military combat soldiers battle 1940s' },
            { url: 'https://rarehistoricalphotos.com/abandoned-places-photos/', title: 'Abandoned Places Photos', tags: 'abandoned places ruins decay urban exploration' },
            { url: 'https://rarehistoricalphotos.com/old-new-york-photos/', title: 'Old New York in Photos', tags: 'new york city vintage old nyc manhattan 1900s 1920s' },
            { url: 'https://rarehistoricalphotos.com/old-london-photos/', title: 'Old London in Photos', tags: 'london england vintage old britain uk victorian' },
            { url: 'https://rarehistoricalphotos.com/space-race-photos/', title: 'Space Race in Photos', tags: 'space race nasa moon apollo astronaut rocket cosmos' },
            { url: 'https://rarehistoricalphotos.com/great-depression-photos/', title: 'Great Depression in Photos', tags: 'great depression poverty 1930s dust bowl economic breadline' },
            { url: 'https://rarehistoricalphotos.com/old-paris-photos/', title: 'Old Paris in Photos', tags: 'paris france vintage eiffel tower old 1900s belle epoque' },
            { url: 'https://rarehistoricalphotos.com/vietnam-war-photos/', title: 'Vietnam War in Photos', tags: 'vietnam war military combat soldiers asia jungle 1960s' },
            { url: 'https://rarehistoricalphotos.com/underwater-detonation-23-kiloton-nuclear-weapon/', title: 'Underwater Detonation of a 23-Kiloton Nuclear Weapon', tags: 'underwater detonation nuclear bomb atomic explosion ocean military' },
            { url: 'https://rarehistoricalphotos.com/first-photograph-upon-discovery-machu-piccu-1911/', title: 'First Photograph Upon Discovery of Machu Picchu 1911', tags: 'machu picchu discovery 1911 inca peru archaeology expedition' },
            { url: 'https://rarehistoricalphotos.com/sydney-opera-house-construction-photos/', title: 'Sydney Opera House Construction Photos', tags: 'sydney opera house construction australia architecture 1960s' },
            { url: 'https://rarehistoricalphotos.com/antarctic-snow-cruiser-photos/', title: 'Antarctic Snow Cruiser Exploration Photos', tags: 'antarctic snow cruiser polar expedition vehicle giant 1939' },
            { url: 'https://rarehistoricalphotos.com/kindertransport-historical-photos/', title: 'Kindertransport Rescue of Jewish Children', tags: 'kindertransport jewish children rescue ww2 holocaust 1938' },
            { url: 'https://rarehistoricalphotos.com/albert-einstein-old-photos/', title: 'Albert Einstein Candid Historical Photos', tags: 'albert einstein scientist genius physics old candid' },
            { url: 'https://rarehistoricalphotos.com/motorcycle-chariot-races-photos/', title: 'Motorcycle Chariot Races from the 1920s and 1930s', tags: 'motorcycle chariot races bizarre sport 1920s 1930s unusual' },

            // ── All That's Interesting: Deep Archival Stories ──
            { url: 'https://allthatsinteresting.com/rare-historical-photos', title: '31 Rare Historical Photos You Had No Idea Even Existed', tags: 'rare historical existed titanic wright brothers lincoln' },
            { url: 'https://allthatsinteresting.com/interesting-photos', title: 'Interesting Historical Photos', tags: 'interesting fascinating unusual surprising history' },
            { url: 'https://allthatsinteresting.com/colorized-historical-photos', title: 'Colorized Historical Photos', tags: 'colorized color historical vintage restored past' },
            { url: 'https://allthatsinteresting.com/old-photos', title: 'Fascinating Old Photos', tags: 'old vintage fascinating photographs century antique' },
            { url: 'https://allthatsinteresting.com/iconic-life-magazine-photos', title: 'Iconic LIFE Magazine Historical Photos', tags: 'life magazine iconic journalism famous photos 20th century' },
            { url: 'https://allthatsinteresting.com/samurai-photos', title: 'Rare Late 19th-Century Samurai Photos', tags: 'samurai japan warriors 19th century vintage armor swords' },
            { url: 'https://allthatsinteresting.com/vintage-deep-sea-diving-photos', title: 'Vintage Deep Sea Diving Photos', tags: 'deep sea diving antique suits underwater ocean helmet' },
            { url: 'https://allthatsinteresting.com/nuclear-testing-photos', title: 'Atomic and Nuclear Testing Historical Photos', tags: 'nuclear testing atomic bomb cold war mushroom cloud' },
            { url: 'https://allthatsinteresting.com/combat-medics-historical-photos', title: 'Historical Combat Medics in Wartime', tags: 'combat medics war hospital military soldiers healthcare' },
            { url: 'https://allthatsinteresting.com/qing-dynasty-photos', title: 'Late Qing Dynasty China Rare Historical Photos', tags: 'qing dynasty china beijing vintage 19th century imperial' },

            // ── History Defined: Extraordinary Moment Vaults ──
            { url: 'https://www.historydefined.net/rare-historical-photos/', title: 'Rare Historical Photos You\'ve Never Seen Before', tags: 'rare unseen historical never seen before history defined' },
            { url: 'https://www.historydefined.net/chilling-rare-historical-photos/', title: 'Chilling Rare Historical Photos', tags: 'chilling eerie haunting rare historical photos dark' },
            { url: 'https://www.historydefined.net/47-haunting-photos-from-history/', title: '47 Haunting Photos from History', tags: 'haunting creepy scary historical photos moments' },
            { url: 'https://www.historydefined.net/must-see-photos-in-history/', title: 'Must-See Photos in History', tags: 'must see iconic famous photos history defined' },
            { url: 'https://www.historydefined.net/civil-war-photos/', title: 'American Civil War Photos', tags: 'civil war american confederate union lincoln soldiers 1860s' },
            { url: 'https://www.historydefined.net/photos-that-tell-incredible-stories/', title: 'Photos That Tell Incredible Stories', tags: 'incredible stories photos amazing storytelling' },
            { url: 'https://www.historydefined.net/historical-photos-that-changed-the-world/', title: 'Historical Photos That Changed the World', tags: 'changed world impactful famous iconic history' },

            // ── 121Clicks: Massive Curated Photo Vaults ──
            { url: 'https://121clicks.com/inspirations/rare-and-weird-history-photos/', title: '35 Rare and Weird History Photos', tags: 'rare weird unusual bizarre vintage strange' },
            { url: 'https://121clicks.com/inspirations/25-rare-historical-photos-part-1/', title: '25 Rare Historical Photos You\'ve Probably Never Seen', tags: 'rare unseen historical photographs 121clicks' },
            { url: 'https://121clicks.com/inspirations/rare-historical-photos-tell-untold-stories/', title: '20 Rare Historical Photos That Tell Untold Stories', tags: 'rare stories untold historical emotional' },
            { url: 'https://121clicks.com/inspirations/rare-historical-photos-past-unfiltered/', title: '36 Rare Historical Photos That Reveal the Human Side of History', tags: 'rare human side history emotional candid' },
            { url: 'https://121clicks.com/inspirations/rare-historical-photos-never-seen-before/', title: '28 Rare Historical Photos That Reveal Stories You\'ve Never Seen Before', tags: 'rare never seen stories reveal' },
            { url: 'https://121clicks.com/inspirations/rare-historical-photos-forgotten-moments/', title: '31 Rare Historical Photos That Bring Forgotten Moments Back Into Focus', tags: 'rare forgotten moments focus antique' },
            { url: 'https://121clicks.com/inspirations/rare-historical-photos-from-100-years-ago/', title: '33 Rare Historical Photos From 100 Years Ago', tags: 'rare 100 years ago vintage old century 1920s' },
            { url: 'https://121clicks.com/inspirations/historical-photos-and-facts-past-to-life/', title: 'Historical Photos and Facts That Bring the Past to Life', tags: 'facts history past life historical photos' },
            { url: 'https://121clicks.com/inspirations/new-york-city-rare-historical-photos/', title: 'New York City Rare Historical Photos', tags: 'new york city nyc manhattan brooklyn vintage old' },
            { url: 'https://121clicks.com/inspirations/rare-photos-from-the-19th-century-history/', title: 'Rare Photos From the 19th Century', tags: '19th century 1800s victorian early photography antique' },

            // ── History Colored & Flashbak ──
            { url: 'https://historycolored.com/articles/5523/50-iconic-and-rare-historical-photographs/', title: '50 Iconic and Rare Historical Photographs', tags: 'iconic rare historical photographs famous historycolored' },
            { url: 'https://flashbak.com/category/photographs/', title: 'Flashbak Historical Photo Archive', tags: 'vintage photographs archive flashbak retro' },
            { url: 'https://www.vintag.es/search?q=rare+historical+photos', title: 'Vintage Everyday Rare Historical Photos', tags: 'rare vintage everyday historical old vintages' }
        ];

        // ─── TIER 2: Site-Specific Search Endpoints ──────────────────────────────
        const SEARCH_ENDPOINTS = [
            { base: 'https://rarehistoricalphotos.com/?s=', domain: 'rarehistoricalphotos.com' },
            { base: 'https://allthatsinteresting.com/?s=', domain: 'allthatsinteresting.com' },
            { base: 'https://www.historydefined.net/?s=', domain: 'historydefined.net' },
            { base: 'https://historycolored.com/?s=', domain: 'historycolored.com' },
            { base: 'https://121clicks.com/?s=', domain: '121clicks.com' },
            { base: 'https://flashbak.com/?s=', domain: 'flashbak.com' },
            { base: 'https://www.vintag.es/search?q=', domain: 'vintag.es' },
        ];

        // Build keyword set from topic for relevance scoring
        const stopWords = new Set(['the','and','for','was','are','not','but','you','have','never','seen','before','rare','photos','photographs','historical','history','inside','that','from','with','most','this','these','those','what','when','where','which','their','about','been','only','into','more','than']);
        const topicWords = topic.toLowerCase()
            .replace(/[^a-z0-9\s]/g, '')
            .split(/\s+/)
            .filter(w => w.length > 2 && !stopWords.has(w));

        console.log(`[Scraper] Direct vault discovery for: "${topic}" (keywords: ${topicWords.join(', ')})`);

        // ─── STEP 1: Score Curated Articles by Topic Relevance ───────────────────
        const scoredCurated = CURATED_PHOTO_ARTICLES.map(art => {
            const searchText = (art.title + ' ' + art.tags).toLowerCase();
            let score = 0;
            for (const kw of topicWords) {
                if (searchText.includes(kw)) score += 3;
            }
            if (searchText.match(/rare|unseen|never|bizarre|unique|forgotten/)) score += 1;
            return { ...art, relevance: score, source: 'curated' };
        });

        scoredCurated.sort((a, b) => b.relevance - a.relevance);

        // ─── STEP 2: Smart Source Rotation (Avoid Repeatedly Scraping Same Article) ─
        // Find highest relevance score
        const highestScore = scoredCurated[0]?.relevance || 0;

        // Filter top scoring candidates that haven't been recently harvested
        let topEligible = scoredCurated.filter(art =>
            art.relevance >= Math.max(3, highestScore - 1) && !recentlyHarvestedUrls.has(art.url)
        );

        // Reset rotation history if all candidates have been visited
        if (topEligible.length === 0) {
            recentlyHarvestedUrls.clear();
            topEligible = scoredCurated.filter(art => art.relevance >= Math.max(3, highestScore - 1));
        }

        let chosenUrl = null;
        let chosenTitle = null;

        if (topEligible.length > 0 && highestScore >= 3) {
            // Pick randomly among top eligible tied articles to ensure variety across runs
            const picked = topEligible[Math.floor(Math.random() * topEligible.length)];
            chosenUrl = picked.url;
            chosenTitle = picked.title;
            console.log(`[Scraper] Curated vault match (score=${picked.relevance}, rotated): "${chosenTitle}" -> ${chosenUrl}`);
        } else {
            // ─── STEP 3: Fallback Search Across Trusted Endpoints ────────────────
            console.log(`[Scraper] No strong curated match. Searching trusted sites for: "${topic}"`);
            const searchQuery = encodeURIComponent(topic.replace(/['"]/g, ''));
            const shuffledSearch = SEARCH_ENDPOINTS.sort(() => Math.random() - 0.5).slice(0, 2);
            let searchResults = [];

            for (const endpoint of shuffledSearch) {
                let searchPage = null;
                try {
                    searchPage = await browser.newPage();
                    const searchUrl = `${endpoint.base}${searchQuery}`;
                    console.log(`[Scraper] Searching: ${searchUrl}`);
                    await searchPage.goto(searchUrl, { waitUntil: 'domcontentloaded', timeout: 15000 });
                    await searchPage.waitForTimeout(1500);

                    const links = await searchPage.evaluate((domain) => {
                        const anchors = Array.from(document.querySelectorAll('article a, h2 a, h3 a, .entry-title a, .post-title a'));
                        const results = [];
                        const seen = new Set();
                        for (const a of anchors) {
                            const href = a.href;
                            const title = a.innerText.trim();
                            if (href && title.length > 10 && !seen.has(href) && href.includes(domain)) {
                                if (!href.includes('/category/') && !href.includes('/tag/') && !href.includes('/author/')) {
                                    seen.add(href);
                                    results.push({ url: href, title });
                                }
                            }
                        }
                        return results.slice(0, 5);
                    }, endpoint.domain);

                    for (const link of links) {
                        const titleLower = link.title.toLowerCase();
                        let rel = 0;
                        for (const kw of topicWords) {
                            if (titleLower.includes(kw)) rel += 3;
                        }
                        searchResults.push({ ...link, relevance: rel, domain: endpoint.domain, source: 'search' });
                    }
                } catch (err) {
                    console.warn(`[Scraper] Search on ${endpoint.domain} failed: ${err.message}`);
                } finally {
                    if (searchPage) await searchPage.close().catch(() => null);
                }
            }

            searchResults.sort((a, b) => b.relevance - a.relevance);

            if (searchResults.length > 0 && searchResults[0].relevance > 0) {
                chosenUrl = searchResults[0].url;
                chosenTitle = searchResults[0].title;
                console.log(`[Scraper] Using search result: "${chosenTitle}"`);
            } else {
                // Fallback to top curated rare photo collection
                const fallbackPool = CURATED_PHOTO_ARTICLES.filter(art => !recentlyHarvestedUrls.has(art.url));
                const fb = (fallbackPool.length > 0 ? fallbackPool : CURATED_PHOTO_ARTICLES)[Math.floor(Math.random() * (fallbackPool.length || CURATED_PHOTO_ARTICLES.length))];
                chosenUrl = fb.url;
                chosenTitle = fb.title;
                console.log(`[Scraper] Fallback to rotated curated article: "${chosenTitle}"`);
            }
        }

        // Add chosen URL to memory tracker
        recentlyHarvestedUrls.add(chosenUrl);
        if (recentlyHarvestedUrls.size > 25) {
            const firstAdded = recentlyHarvestedUrls.values().next().value;
            recentlyHarvestedUrls.delete(firstAdded);
        }

        console.log(`[Scraper] Chosen article: "${chosenTitle}" -> ${chosenUrl}`);

        // Scrape the chosen article using universal photo & story extractor
        page = await browser.newPage();
        const items = await extractArticlePhotos(page, chosenUrl);
        console.log(`[Scraper] Scraped ${items.length} items from article "${chosenUrl}"`);

        res.json({
            status: 'ok',
            topic: topic,
            article_url: chosenUrl,
            items: items.slice(0, limit * 2)
        });

    } catch (err) {
        console.error(`[Scraper] Article discovery failed for "${topic}":`, err.message);
        res.status(500).json({ status: 'error', topic, error: err.message, items: [] });
    } finally {
        if (page) await page.close().catch(() => null);
    }
});

// ── Clean Shutdown Endpoint ──────────────────────────────────────────────────
app.all('/shutdown', async (req, res) => {
    console.log('[Scraper API] Shutdown request received. Closing persistent browser context...');
    try {
        if (defaultPage) {
            await defaultPage.close().catch(() => null);
            defaultPage = null;
        }
        if (persistentBrowser) {
            await persistentBrowser.close().catch(() => null);
            persistentBrowser = null;
        }
        res.json({ status: 'ok', message: 'Stealth browser context successfully closed.' });
        setTimeout(() => process.exit(0), 500);
    } catch (err) {
        res.status(500).json({ status: 'error', error: err.message });
    }
});

// ── Server Bootstrap ─────────────────────────────────────────────────────────
app.listen(PORT, '127.0.0.1', async () => {
    console.log(`[Scraper API] Stealth search microservice listening on http://127.0.0.1:${PORT}`);
    try {
        await getBrowser();
    } catch (err) {
        console.warn(`[Scraper API] Background browser pre-warm warning: ${err.message}`);
    }
});

// Graceful shutdown
process.on('SIGTERM', async () => {
    console.log('[Scraper API] Shutting down...');
    if (persistentBrowser) {
        await persistentBrowser.close().catch(() => null);
    }
    process.exit(0);
});
process.on('SIGINT', async () => {
    console.log('[Scraper API] Interrupted...');
    if (persistentBrowser) {
        await persistentBrowser.close().catch(() => null);
    }
    process.exit(0);
});
