from django.http import JsonResponse

def home(request):
    return JsonResponse({
        "message": "Welcome to the Route Fuel API!",
        "usage": "Send POST request to /api/route/ with JSON { start_address, end_address }"
    })
