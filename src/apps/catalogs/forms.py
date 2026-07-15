from django import forms


class CatalogUploadForm(forms.Form):
    name = forms.CharField(max_length=200, label="Nombre visible")
    file = forms.FileField(label="PDF")
