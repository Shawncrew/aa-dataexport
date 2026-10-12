from django.urls import path

from . import views

app_name = "dataexport"

urlpatterns = [
    path("api/v1/", views.index, name="index"),
    path("api/v1/<slug:slug>/", views.endpoint, name="endpoint"),
]
