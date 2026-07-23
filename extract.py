import re
with open('c:/anti/2/static/studio.html', 'r', encoding='utf-8') as f:
    studio_html = f.read()

# Extract CSS
css_match = re.search(r'/\* ========================= LEFT NAV PANEL ========================= \*/(.*?)/* ========================= CENTER GENERATION PANEL ========================= \*/', studio_html, re.DOTALL)
css = css_match.group(0) if css_match else ""

# Extract Header
header_match = re.search(r'<header>.*?</header>', studio_html, re.DOTALL)
header = header_match.group(0) if header_match else ""

# Extract Left Panel
left_match = re.search(r'<aside class="left-panel">.*?</aside>', studio_html, re.DOTALL)
left_panel = left_match.group(0) if left_match else ""

# Extract Scripts
scripts = """
function switchMode(name) {
    if (name === 'studio') window.location.href = '/motionpix';
}
function handleLogout() {
    window.location.href = '/login';
}
"""

with open('c:/anti/2/static/canvas.html', 'r', encoding='utf-8') as f:
    canvas_html = f.read()

# Insert CSS before </style>
canvas_html = canvas_html.replace('</style>', f'\n{css}\n</style>')

# Replace <body> layout
body_start = canvas_html.find('<body>') + 6
body_inner = f"""
{header}
<div class="app-body" style="display: flex; height: calc(100vh - 56px);">
{left_panel}
"""
canvas_html = canvas_html[:body_start] + body_inner + canvas_html[body_start:]

# Close the .app-body div before </body>
body_end = canvas_html.find('</body>')
canvas_html = canvas_html[:body_end] + "</div>\n<script>\n" + scripts + "</script>\n" + canvas_html[body_end:]

# Update the #root element to match center-panel styles
canvas_html = canvas_html.replace('id="root"', 'id="root" style="flex: 1; position: relative; height: 100%;"')

with open('c:/anti/2/static/canvas_new.html', 'w', encoding='utf-8') as f:
    f.write(canvas_html)

print("Done generating canvas_new.html")
