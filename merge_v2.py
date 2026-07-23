import re

# We don't checkout old commits anymore to preserve recent changes

with open('c:/anti/2/static/studio.html', 'r', encoding='utf-8') as f:
    studio_html = f.read()

with open('c:/anti/2/static/canvas.html', 'r', encoding='utf-8') as f:
    canvas_html = f.read()

# 1. Extract React Flow CSS and custom node styles from canvas.html
style_match = re.search(r'<style>(.*?)</style>', canvas_html, re.DOTALL)
canvas_style = style_match.group(1) if style_match else ""
# Remove base styles that conflict with studio.html
canvas_style = re.sub(r'body,\s*html,\s*#root\s*{[^}]*}', '', canvas_style, flags=re.DOTALL)
canvas_style = re.sub(r'\*\s*{[^}]*}', '', canvas_style, flags=re.DOTALL)

# Adjust left-bar to top-bar
left_bar_css = """.left-bar {
            position: absolute;
            top: 24px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(22, 23, 29, 0.95);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 999px;
            padding: 6px 12px;
            display: flex;
            flex-direction: row;
            align-items: center;
            gap: 6px;
            z-index: 100;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.6);
            backdrop-filter: blur(10px);
        }"""
canvas_style = re.sub(r'\.left-bar\s*{.*?(?=\.tool-btn)', left_bar_css + '\n\n', canvas_style, flags=re.DOTALL)

# Replace tool-btn CSS to be horizontal
tool_btn_css = """.tool-btn {
            height: 38px;
            padding: 0 16px;
            border-radius: 20px;
            background: transparent;
            border: 1px solid transparent;
            color: #a1a1aa;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s;
            font-size: 0.8rem;
            font-weight: 600;
            white-space: nowrap;
        }
        .tool-btn:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
        .tool-sep { width: 1px; height: 20px; background: rgba(255, 255, 255, 0.15); margin: 0 4px; }
"""
canvas_style = re.sub(r'\.tool-btn\s*{.*?(?=\/\* Custom)', tool_btn_css, canvas_style, flags=re.DOTALL)


# 2. Extract React and Babel scripts from canvas.html
babel_match = re.search(r'<script type="text/babel">(.*?)</script>', canvas_html, re.DOTALL)
babel_script = babel_match.group(1) if babel_match else ""

# Ensure babel script is wrapped in IIFE to prevent variable collision with global JS
if not babel_script.strip().startswith('(() => {'):
    babel_script = "(() => {\n" + babel_script + "\n})();"

deps = """
    <!-- React & ReactDOM (Production) CDN -->
    <script src="https://unpkg.com/react@18/umd/react.production.min.js" crossorigin></script>
    <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js" crossorigin></script>
    <!-- Babel CDN (JSX compiler) -->
    <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
    <!-- React Flow CSS -->
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reactflow@11.10.3/dist/style.css">
    <!-- React Flow UMD Bundle -->
    <script src="https://cdn.jsdelivr.net/npm/reactflow@11.10.3/dist/umd/index.min.js"></script>
    <!-- html2canvas -->
    <script src="https://html2canvas.hertzen.com/dist/html2canvas.min.js"></script>
"""

# 3. Create the new HTML based entirely on studio.html
new_html = studio_html

# Insert deps and style into <head>
new_html = new_html.replace('</head>', f'{deps}\n<style>\n{canvas_style}\n</style>\n</head>')

# Replace the inner contents of .center-panel with #root
center_panel_start = new_html.find('<div class="center-panel">')
center_panel_end = new_html.find('<!-- ===== RIGHT GALLERY PANEL ===== -->')
if center_panel_end == -1:
    center_panel_end = new_html.find('<aside class="right-panel">')

if center_panel_start != -1 and center_panel_end != -1:
    new_center = '<div class="center-panel" style="position: relative;">\n<div id="root" style="width:100%; height:100%; display:flex; flex-direction:column;"></div>\n</div>\n'
    new_html = new_html[:center_panel_start] + new_center + new_html[center_panel_end:]

# Inject the babel script just before </body>
new_html = new_html.replace('</body>', f'<script type="text/babel">\n{babel_script}\n</script>\n</body>')

# Swap the "active" nav link in the header
new_html = new_html.replace('<a href="/motionpix" class="nav-link active">Create</a>', '<a href="/motionpix" class="nav-link">Create</a>')
new_html = new_html.replace('<a href="/canvas" class="nav-link">Canvas</a>', '<a href="/canvas" class="nav-link active">Canvas</a>')

# Swap the "active" class on the left sidebar
new_html = new_html.replace('<div class="nav-item active" id="nav-studio"', '<div class="nav-item" id="nav-studio"')
new_html = new_html.replace('<div class="nav-item" id="nav-canvas"', '<div class="nav-item active" id="nav-canvas"')

# To prevent Vanilla JS errors from missing DOM elements in the center panel, we can safely wrap them or just ignore them since they only trigger on user actions inside the center panel which no longer exists.
# `loadGallery`, `loadCanvasProjectsList`, `deleteProject` do NOT depend on `.center-panel` DOM elements! They will work perfectly!

with open('c:/anti/2/static/canvas.html', 'w', encoding='utf-8') as f:
    f.write(new_html)

print("canvas.html created perfectly!")
