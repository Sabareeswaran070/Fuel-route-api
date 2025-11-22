from django.contrib import admin
from django.urls import path, include
from api.home import home

urlpatterns = [
    path('', home),                      # Homepage
    path('admin/', admin.site.urls),
    path('api/', include('api.urls')),   # API routes
]
