
import os

file_path = r'c:\Users\Henrian\Desktop\django\pisowifi\dashboard\templates\dashboard\sessions.html'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

div_open = content.count('<div')
div_close = content.count('</div>')
form_open = content.count('<form')
form_close = content.count('</form>')
block_open = content.count('{% block')
block_close = content.count('{% endblock')

print(f"DIV: {div_open} open, {div_close} close")
print(f"FORM: {form_open} open, {form_close} close")
print(f"BLOCK: {block_open} open, {block_close} close")
