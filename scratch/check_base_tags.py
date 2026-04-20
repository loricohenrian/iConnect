
import os

file_path = r'c:\Users\Henrian\Desktop\django\pisowifi\templates\base_dashboard.html'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

div_open = content.count('<div')
div_close = content.count('</div>')
header_open = content.count('<header')
header_close = content.count('</header>')
aside_open = content.count('<aside')
aside_close = content.count('</aside>')
main_open = content.count('<main')
main_close = content.count('</main>')

print(f"DIV: {div_open} open, {div_close} close")
print(f"HEADER: {header_open} open, {header_close} close")
print(f"ASIDE: {aside_open} open, {aside_close} close")
print(f"MAIN: {main_open} open, {main_close} close")
