"""/w/ — the front page: "Facit · 246", a scroll story about the toothpicks in Rain Man.

Server-rendered in five languages (da, en, de, sv, no); the interactions live in web_static/facit.js
(the CSP allows no inline scripts). The page always ends with the login card. Design reference:
specs/forside-246/.
"""

import math
from html import escape

from app import og

LANGS = ("da", "en", "de", "sv", "no")
HTML_LANG = {"no": "nb"}
LANG_CODES = {"da": "DA", "en": "EN", "de": "DE", "sv": "SV", "no": "NO"}

TEXT = {
    "da": {
        "tap": "tryk", "lang_nav": "Sprog",
        "lines": ["246.", "Præcis 246.", "Det er facit.", "Mellemregningen? Den ligger længere nede."],
        "drop": "En servitrice taber en æske tandstikkere.",
        "scatter": "De spreder sig ud over gulvet. Raymond kigger — et halvt sekund. Så siger han tre tal. Og så ét til.",
        "countOn": "Tæl dem", "countOff": "Skjul",
        "knows": "Han tæller ikke. Han ved det. Ingen andre i rummet kan tjekke ham — så de må enten tro på ham eller lade være.",
        "workEyebrow": "Mellemregningen",
        "boxSays": "Men der står 250 på æsken.",
        "turns": "Servitricen vender æsken. 250 stk. står der. Så kigger hun ned i den.",
        "row1": "står der på æsken", "row2": "ligger stadig i æsken", "row3": "ligger på gulvet",
        "workOn": "Vis mellemregningen", "workOff": "Skjul mellemregningen",
        "right": "Han havde ret. Han gad bare ikke vise regnestykket.",
        "boxLabel": "TANDSTIKKERE", "boxCount": "250 stk.", "left": "4 tilbage",
        "first": "Facit først. Forklaringen — måske.",
        "aloud": "Raymond regnede ikke forkert. Han regnede bare ikke højt.",
        "minds": "Sådan arbejder mange autistiske hjerner: svaret står klart, og mellemregningen føles overflødig — den var jo åbenlys. Det er ikke uhøflighed. Det er bare en anden vej til det samme facit.",
        "ask": "Det eneste, det kræver, er én, der gider spørge: ", "how": "hvordan ved du det?",
        "hm": "Hos Happy Mates arbejder vi med at integrere autister — og andre, der måske er lidt skæve.",
        "more": "Læs mere på happymates.ai", "next": "Videre", "top": "Forfra",
        "login": "Log ind", "card_title": "Log ind", "privacy": "Privatliv",
        "card_text": "Brug din e-mailadresse. Du får en engangskode på mail.",
        "first_user": "Instansen er ny og har ingen brugere endnu. Den første, der logger ind, bliver administrator.",
    },
    "en": {
        "tap": "tap", "lang_nav": "Language",
        "lines": ["246.", "Exactly 246.", "That’s the answer.", "The working? It’s further down."],
        "drop": "A waitress drops a box of toothpicks.",
        "scatter": "They scatter across the floor. Raymond looks — for half a second. Then he says three numbers. And then one more.",
        "countOn": "Count them", "countOff": "Hide",
        "knows": "He doesn’t count. He knows. Nobody else in the room can check him — so they either believe him or they don’t.",
        "workEyebrow": "The working",
        "boxSays": "But the box says 250.",
        "turns": "The waitress turns the box over. 250, it says. Then she looks inside.",
        "row1": "on the box", "row2": "still in the box", "row3": "on the floor",
        "workOn": "Show the working", "workOff": "Hide the working",
        "right": "He was right. He just couldn’t be bothered to show his working.",
        "boxLabel": "TOOTHPICKS", "boxCount": "250 pcs", "left": "4 left",
        "first": "The answer first. The explanation — maybe.",
        "aloud": "Raymond didn’t get it wrong. He just didn’t think out loud.",
        "minds": "That’s how many autistic minds work: the answer is clear, and the working feels redundant — it was obvious, after all. It isn’t rudeness. It’s just a different route to the same answer.",
        "ask": "All it takes is someone who bothers to ask: ", "how": "how do you know?",
        "hm": "At Happy Mates we work to include autistic people — and others who might be a little offbeat.",
        "more": "Read more at happymates.ai", "next": "Continue", "top": "Start over",
        "login": "Log in", "card_title": "Log in", "privacy": "Privacy",
        "card_text": "Use your e-mail address. We'll e-mail you a one-time code.",
        "first_user": "This instance is new and has no users yet. The first person to log in becomes the administrator.",
    },
    "de": {
        "tap": "tippen", "lang_nav": "Sprache",
        "lines": ["246.", "Genau 246.", "Das ist das Ergebnis.", "Der Rechenweg? Der kommt weiter unten."],
        "drop": "Eine Kellnerin lässt eine Schachtel Zahnstocher fallen.",
        "scatter": "Sie verteilen sich über den Boden. Raymond schaut hin — eine halbe Sekunde. Dann sagt er drei Zahlen. Und dann noch eine.",
        "countOn": "Zähl sie", "countOff": "Ausblenden",
        "knows": "Er zählt nicht. Er weiß es. Niemand sonst im Raum kann es nachprüfen — also glauben sie ihm oder eben nicht.",
        "workEyebrow": "Der Rechenweg",
        "boxSays": "Aber auf der Schachtel steht 250.",
        "turns": "Die Kellnerin dreht die Schachtel um. 250 Stück steht darauf. Dann schaut sie hinein.",
        "row1": "steht auf der Schachtel", "row2": "liegen noch in der Schachtel", "row3": "liegen auf dem Boden",
        "workOn": "Rechenweg zeigen", "workOff": "Rechenweg ausblenden",
        "right": "Er hatte recht. Er hatte nur keine Lust, den Rechenweg zu zeigen.",
        "boxLabel": "ZAHNSTOCHER", "boxCount": "250 Stück", "left": "4 übrig",
        "first": "Erst das Ergebnis. Die Erklärung — vielleicht.",
        "aloud": "Raymond hat sich nicht verrechnet. Er hat nur nicht laut gerechnet.",
        "minds": "So arbeiten viele autistische Köpfe: Die Antwort ist klar, und der Rechenweg fühlt sich überflüssig an — er war ja offensichtlich. Das ist keine Unhöflichkeit. Es ist nur ein anderer Weg zum selben Ergebnis.",
        "ask": "Es braucht nur jemanden, der nachfragt: ", "how": "Woher weißt du das?",
        "hm": "Bei Happy Mates integrieren wir Autistinnen und Autisten — und andere, die vielleicht ein bisschen schräg sind.",
        "more": "Mehr auf happymates.ai", "next": "Weiter", "top": "Von vorn",
        "login": "Anmelden", "card_title": "Anmelden", "privacy": "Datenschutz",
        "card_text": "Mit deiner E-Mail-Adresse. Du bekommst einen Einmalcode per E-Mail.",
        "first_user": "Diese Instanz ist neu und hat noch keine Nutzer. Wer sich zuerst anmeldet, wird Administrator.",
    },
    "sv": {
        "tap": "tryck", "lang_nav": "Språk",
        "lines": ["246.", "Exakt 246.", "Det är facit.", "Uträkningen? Den ligger längre ner."],
        "drop": "En servitris tappar en ask tandpetare.",
        "scatter": "De sprids över golvet. Raymond tittar — en halv sekund. Sen säger han tre tal. Och sen ett till.",
        "countOn": "Räkna dem", "countOff": "Dölj",
        "knows": "Han räknar inte. Han vet. Ingen annan i rummet kan kontrollera honom — så de får antingen tro på honom eller låta bli.",
        "workEyebrow": "Uträkningen",
        "boxSays": "Men det står 250 på asken.",
        "turns": "Servitrisen vänder på asken. 250 st. står det. Sen tittar hon ner i den.",
        "row1": "står på asken", "row2": "ligger kvar i asken", "row3": "ligger på golvet",
        "workOn": "Visa uträkningen", "workOff": "Dölj uträkningen",
        "right": "Han hade rätt. Han brydde sig bara inte om att visa uträkningen.",
        "boxLabel": "TANDPETARE", "boxCount": "250 st.", "left": "4 kvar",
        "first": "Facit först. Förklaringen — kanske.",
        "aloud": "Raymond räknade inte fel. Han räknade bara inte högt.",
        "minds": "Så fungerar många autistiska hjärnor: svaret är klart, och uträkningen känns överflödig — den var ju självklar. Det är inte oartigt. Det är bara en annan väg till samma facit.",
        "ask": "Det enda som krävs är någon som orkar fråga: ", "how": "hur vet du det?",
        "hm": "På Happy Mates arbetar vi med att integrera autister — och andra som kanske är lite udda.",
        "more": "Läs mer på happymates.ai", "next": "Vidare", "top": "Från början",
        "login": "Logga in", "card_title": "Logga in", "privacy": "Integritet",
        "card_text": "Använd din e-postadress. Du får en engångskod via e-post.",
        "first_user": "Instansen är ny och har inga användare än. Den som loggar in först blir administratör.",
    },
    "no": {
        "tap": "trykk", "lang_nav": "Språk",
        "lines": ["246.", "Nøyaktig 246.", "Det er fasit.", "Utregningen? Den ligger lenger ned."],
        "drop": "En servitør mister en eske tannpirkere.",
        "scatter": "De sprer seg utover gulvet. Raymond ser — et halvt sekund. Så sier han tre tall. Og så ett til.",
        "countOn": "Tell dem", "countOff": "Skjul",
        "knows": "Han teller ikke. Han vet det. Ingen andre i rommet kan sjekke ham — så de må enten tro på ham eller la være.",
        "workEyebrow": "Utregningen",
        "boxSays": "Men det står 250 på esken.",
        "turns": "Servitøren snur esken. 250 stk. står det. Så ser hun ned i den.",
        "row1": "står på esken", "row2": "ligger fortsatt i esken", "row3": "ligger på gulvet",
        "workOn": "Vis utregningen", "workOff": "Skjul utregningen",
        "right": "Han hadde rett. Han gadd bare ikke vise regnestykket.",
        "boxLabel": "TANNPIRKERE", "boxCount": "250 stk.", "left": "4 igjen",
        "first": "Fasit først. Forklaringen — kanskje.",
        "aloud": "Raymond regnet ikke feil. Han regnet bare ikke høyt.",
        "minds": "Slik jobber mange autistiske hjerner: svaret er klart, og utregningen føles overflødig — den var jo opplagt. Det er ikke uhøflighet. Det er bare en annen vei til samme fasit.",
        "ask": "Det eneste som trengs, er én som gidder å spørre: ", "how": "hvordan vet du det?",
        "hm": "Hos Happy Mates jobber vi med å inkludere autister — og andre som kanskje er litt skjeve.",
        "more": "Les mer på happymates.ai", "next": "Videre", "top": "Forfra",
        "login": "Logg inn", "card_title": "Logg inn", "privacy": "Personvern",
        "card_text": "Bruk e-postadressen din. Du får en engangskode på e-post.",
        "first_user": "Instansen er ny og har ingen brukere ennå. Den første som logger inn, blir administrator.",
    },
}

CENTERS = ((20, 52), (50, 38), (79, 58))  # three piles of 82, in % of the floor


def _sticks() -> str:
    """246 toothpicks in three piles, from a seeded PRNG (mulberry32, seed 246) so the floor never changes."""
    m = 0xFFFFFFFF
    seed = 246

    def rnd() -> float:
        nonlocal seed
        seed = (seed + 0x6D2B79F5) & m
        t = ((seed ^ (seed >> 15)) * (1 | seed)) & m
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & m)) & m) ^ t
        return (t ^ (t >> 14)) / 4294967296

    out = []
    for cl, ct in CENTERS:
        for _ in range(82):
            a = rnd() * math.pi * 2
            d = math.sqrt(rnd())
            left = max(1, min(95, cl + math.cos(a) * d * 13))
            top = max(2, min(96, ct + math.sin(a) * d * 30))
            rot = round(rnd() * 180)
            width = 22 + round(rnd() * 14)
            color = "#E2C79A" if rnd() > 0.5 else "#C9A877"
            opacity = 0.7 + rnd() * 0.3
            out.append(f'<i style="left:{left:.2f}%;top:{top:.2f}%;width:{width}px;'
                       f'background:{color};transform:rotate({rot}deg);opacity:{opacity:.2f}"></i>')
    return "".join(out)


STICKS = _sticks()


def _toggle_label(on: str, off: str) -> str:
    return f'<span class="when-off">{escape(on)}</span><span class="when-on">{escape(off)}</span>'


def render(*, lang: str, app_name: str, base: str, nav: list[dict], first_user: bool, og_image: str = "") -> str:
    t = TEXT[lang]
    e = {k: escape(v) for k, v in t.items() if isinstance(v, str)}
    name = escape(app_name)
    langs = "".join(
        f'<a class="lng" href="/w/?lang={code}" hreflang="{HTML_LANG.get(code, code)}"'
        f'{" aria-current=\"true\"" if code == lang else ""}>{LANG_CODES[code]}</a>'
        for code in LANGS)
    alternates = "".join(f'<link rel="alternate" hreflang="{HTML_LANG.get(c, c)}" href="{base}/w/?lang={c}">'
                         for c in LANGS)
    whispers = "".join(f'<p class="whisper" hidden>{escape(line)}</p>' for line in t["lines"])
    clusters = "".join(f'<div class="cluster" style="left:{cl}%;top:{ct}%"><span aria-hidden="true"></span><b>82</b></div>'
                       for cl, ct in CENTERS)
    links = "".join(f'<a href="/w/{escape(p["slug"])}">{escape(p["title"])}</a>' for p in nav if p["slug"])
    notice = f'<p class="first">{e["first_user"]}</p>' if first_user else ""
    chevron = ('<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
               'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="{d}"></path></svg>')
    return f"""<!doctype html>
<html lang="{HTML_LANG.get(lang, lang)}">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>246 · {name}</title>
<meta name="description" content="{e['first']} {e['aloud']}">
{og.meta_tags(title="246", description=f"{t['first']} {t['aloud']}", site_name=app_name, image=og_image,
              url=f"{base}/w/?lang={lang}")}
<meta name="theme-color" content="#020817">
<link rel="canonical" href="{base}/w/?lang={lang}">{alternates}
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@200;300;400;500;600;700&display=swap">
<link rel="stylesheet" href="/w/static/w.css">
<link rel="stylesheet" href="/w/static/facit.css">
<script src="/w/static/w.js" defer></script>
<script src="/w/static/facit.js" defer></script>
</head>
<body class="facit">
<main>

<section id="top" class="hero">
<nav class="langs" aria-label="{e['lang_nav']}">{langs}</nav>
<button type="button" class="num appear" id="facit-num" aria-label="246">246</button>
<div class="say" aria-live="polite">
<p class="late tap">{e['tap']}</p>
{whispers}
</div>
</section>

<section id="gulvet" class="floor-sec">
<div class="wrap">
<div class="reveal text">
<p class="eyebrow">Rain Man, 1988</p>
<h2>{e['drop']}</h2>
<p class="lead">{e['scatter']}</p>
</div>
<div class="reveal floor">{STICKS}<div class="clusters">{clusters}</div></div>
<div class="reveal row">
<button type="button" class="pill pill-dark" data-toggle="gulvet" aria-pressed="false">{_toggle_label(t['countOn'], t['countOff'])}</button>
<p class="sum">82 + 82 + 82 = <strong>246</strong></p>
</div>
<p class="reveal lead text">{e['knows']}</p>
</div>
</section>

<section id="aesken" class="box-sec">
<div class="wrap split">
<div class="reveal col">
<p class="eyebrow">{e['workEyebrow']}</p>
<h2>{e['boxSays']}</h2>
<p class="lead">{e['turns']}</p>
<div class="sheet">
<div class="line"><span class="v"><span class="when-off">?</span><span class="when-on">250</span></span><span>{e['row1']}</span></div>
<div class="line"><span class="v"><span class="when-off">− ?</span><span class="when-on">− 4</span></span><span>{e['row2']}</span></div>
<div class="line total"><span class="v">= 246</span><span>{e['row3']}</span></div>
</div>
<div><button type="button" class="pill pill-blue" data-toggle="aesken" aria-pressed="false">{_toggle_label(t['workOn'], t['workOff'])}</button></div>
<p class="right when-on">{e['right']}</p>
</div>
<div class="reveal col box-col">
<div class="box">
<div class="glow" aria-hidden="true"></div>
<div class="card">
<div class="head"><span class="label">{e['boxLabel']}</span><span class="count">{e['boxCount']}</span></div>
<div class="inside">
<i aria-hidden="true" style="left:22%;top:40%;transform:rotate(-8deg)"></i>
<i aria-hidden="true" style="left:30%;top:52%;transform:rotate(5deg);background:#CDAE7C"></i>
<i aria-hidden="true" style="left:18%;top:63%;transform:rotate(-3deg)"></i>
<i aria-hidden="true" style="left:34%;top:72%;transform:rotate(11deg);background:#CDAE7C"></i>
<span class="left when-on">{e['left']}</span>
</div>
</div>
</div>
</div>
</div>
</section>

<section id="facit" class="end-sec">
<div class="narrow">
<h2 class="reveal">{e['first']}</h2>
<p class="reveal">{e['aloud']}</p>
<p class="reveal">{e['minds']}</p>
<p class="reveal strong">{e['ask']}<em>{e['how']}</em></p>
</div>
<p class="reveal echo" aria-hidden="true">246</p>
<section class="login-card">
  <h2>{e['card_title']}</h2>
  <p>{e['card_text']}</p>
  {notice}
  <a class="button" href="/login">{e['login']}</a>
</section>
<footer class="foot">
<p>{e['hm']}</p>
<a class="more" href="https://happymates.ai"><span>{e['more']}</span><span class="rb" aria-hidden="true"></span></a>
<nav class="site" aria-label="{name}">{links}<a href="/privatliv">{e['privacy']}</a></nav>
<p class="copy">© {name}</p>
</footer>
</section>

<button type="button" class="pill float" id="facit-go" hidden>
<span class="go-next">{e['next']}</span><span class="go-top">{e['top']}</span>
<span class="bob go-next">{chevron.format(d="m6 9 6 6 6-6")}</span><span class="go-top">{chevron.format(d="m18 15-6-6-6 6")}</span>
</button>

</main>
</body>
</html>"""
