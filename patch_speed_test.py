import sys

views_file = 'sessions_app/views.py'
with open(views_file, 'r') as f:
    content = f.read()

new_views = """
from django.http import StreamingHttpResponse
import os

@api_view(["GET"])
@permission_classes([AllowAny])
def speed_test_download(request):
    \"\"\"
    Endpoint for performing a real download speed test.
    Streams 10MB of random data.
    \"\"\"
    chunk_size = 65536
    chunks = (10 * 1024 * 1024) // chunk_size

    def stream_random_data():
        for _ in range(chunks):
            yield os.urandom(chunk_size)

    response = StreamingHttpResponse(stream_random_data(), content_type="application/octet-stream")
    response['Content-Length'] = str(10 * 1024 * 1024)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

@api_view(["POST"])
@permission_classes([AllowAny])
def speed_test_upload(request):
    \"\"\"
    Endpoint for performing a real upload speed test.
    Accepts arbitrary data and returns 200 OK.
    \"\"\"
    return Response({"status": "success", "message": "Upload test completed"}, status=status.HTTP_200_OK)
"""

if "def speed_test_download" not in content:
    with open(views_file, 'a') as f:
        f.write(new_views)

urls_file = 'sessions_app/urls.py'
with open(urls_file, 'r') as f:
    urls_content = f.read()

if "speed-test-download" not in urls_content:
    urls_content = urls_content.replace(
        "path('speed-test/', views.speed_test, name='speed-test'),",
        "path('speed-test/', views.speed_test, name='speed-test'),\n    path('speed-test-download/', views.speed_test_download, name='speed-test-download'),\n    path('speed-test-upload/', views.speed_test_upload, name='speed-test-upload'),"
    )
    with open(urls_file, 'w') as f:
        f.write(urls_content)

print("Speed test endpoints added successfully.")
