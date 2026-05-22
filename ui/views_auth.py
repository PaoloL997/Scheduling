"""Authentication views: registration, login and logout."""

from django import forms
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render


class RegistrationForm(UserCreationForm):
    """Extended user creation form that collects nome, cognome and email.

    Inherits password1/password2 validation from :class:`UserCreationForm`
    and adds first_name, last_name and email as required fields.
    """

    first_name = forms.CharField(
        max_length=150,
        label="Nome",
        widget=forms.TextInput(
            attrs={"placeholder": "Mario", "autocomplete": "given-name"}
        ),
    )
    last_name = forms.CharField(
        max_length=150,
        label="Cognome",
        widget=forms.TextInput(
            attrs={"placeholder": "Rossi", "autocomplete": "family-name"}
        ),
    )
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(
            attrs={"placeholder": "mario@esempio.it", "autocomplete": "email"}
        ),
    )

    class Meta:
        """Form metadata linking to the built-in User model."""

        model = User
        fields = (
            "first_name",
            "last_name",
            "username",
            "email",
            "password1",
            "password2",
        )
        widgets = {
            "username": forms.TextInput(
                attrs={"placeholder": "mario.rossi", "autocomplete": "username"}
            ),
        }

    def save(self, commit: bool = True) -> User:
        """Save the user with first_name, last_name and email populated.

        Args:
            commit (bool): Whether to persist the instance immediately.

        Returns:
            User: The newly created user instance.
        """
        user = super().save(commit=False)
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user


def register_view(request: HttpRequest) -> HttpResponse:
    """Render the registration page and process new account creation.

    On GET returns the blank registration form.  On POST validates the form,
    creates the user, logs them in automatically and redirects to the home page.

    Args:
        request: The incoming HTTP request.

    Returns:
        HttpResponse: Redirect to ``/`` on success; re-rendered form on failure.
    """
    if request.user.is_authenticated:
        return redirect("/")

    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("/")

    return render(request, "ui/register.html", {"form": form})


def login_view(request: HttpRequest) -> HttpResponse:
    """Render the login page and authenticate returning users.

    On GET returns the blank login form.  On POST authenticates with
    ``username`` and ``password``; on success logs the user in and redirects
    to the ``next`` query-string parameter (defaulting to ``/``).

    Args:
        request: The incoming HTTP request.

    Returns:
        HttpResponse: Redirect to the next page on success; re-rendered form
            with an error message on failure.
    """
    if request.user.is_authenticated:
        return redirect("/")

    error = ""
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            next_url = request.GET.get("next", "/")
            return redirect(next_url)
        error = "Username o password non corretti."

    return render(request, "ui/login.html", {"error": error})


def logout_view(request: HttpRequest) -> HttpResponse:
    """Log the current user out and redirect to the login page.

    Only POST requests are accepted to prevent CSRF-based logout attacks
    triggered by a simple ``<img>`` tag or link.

    Args:
        request: The incoming HTTP POST request.

    Returns:
        HttpResponse: Redirect to ``/login/``.
    """
    if request.method == "POST":
        logout(request)
    return redirect("/login/")
