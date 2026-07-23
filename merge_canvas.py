import re

with open('c:/anti/2/static/studio.html', 'r', encoding='utf-8') as f:
    studio_html = f.read()

with open('c:/anti/2/static/canvas.html', 'r', encoding='utf-8') as f:
    canvas_html = f.read()

# Extract head scripts from canvas.html
head_match = re.search(r'<head>(.*?)</head>', canvas_html, re.DOTALL)
canvas_head = head_match.group(1) if head_match else ""

# Extract the style block specifically for the React Flow nodes and tool bar
style_match = re.search(r'<style>(.*?)</style>', canvas_html, re.DOTALL)
canvas_style = style_match.group(1) if style_match else ""

# Remove body/html/root CSS from canvas_style to avoid overriding studio.html
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


# Extract Babel script
babel_match = re.search(r'<script type="text/babel">(.*?)</script>', canvas_html, re.DOTALL)
babel_script = babel_match.group(1) if babel_match else ""

# 1. Insert React dependencies into <head>
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
"""
studio_html = studio_html.replace('</head>', f'{deps}\n<style>\n{canvas_style}\n</style>\n</head>')

# 2. Replace the inside of .center-panel with #root
center_panel_start = studio_html.find('<div class="center-panel">')
center_panel_end = studio_html.find('<!-- Right History Panel -->', center_panel_start)
if center_panel_end == -1:
    center_panel_end = studio_html.find('<aside class="right-panel">', center_panel_start)

# We want to replace everything inside <div class="center-panel"> ... </div>
new_center = '<div class="center-panel" style="position: relative;">\n<div id="root" style="width:100%; height:100%; display:flex; flex-direction:column;"></div>\n</div>\n'
studio_html = studio_html[:center_panel_start] + new_center + studio_html[center_panel_end:]

# 3. Append Babel script
studio_html = studio_html.replace('</body>', f'<script type="text/babel">\n{babel_script}\n</script>\n</body>')

# 4. Remove active class from 'Create' and add to 'Canvas' in header nav
studio_html = studio_html.replace('<a href="/motionpix" class="nav-link active">Create</a>', '<a href="/motionpix" class="nav-link">Create</a>')
studio_html = studio_html.replace('<a href="/canvas" class="nav-link">Canvas</a>', '<a href="/canvas" class="nav-link active">Canvas</a>')

# Save to canvas.html
with open('c:/anti/2/static/canvas.html', 'w', encoding='utf-8') as f:
    f.write(studio_html)

print("canvas.html merged successfully!")
