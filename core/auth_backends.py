from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class UsernameOrEmailBackend(ModelBackend):
    """
    Allows authentication with either username or e-mail in the username field.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if username is None or password is None:
            return None

        UserModel = get_user_model()
        lookup = {"email__iexact": username} if "@" in username else {"username": username}
        try:
            user = UserModel.objects.get(**lookup)
        except UserModel.DoesNotExist:
            # Run hasher once to reduce timing difference with valid users.
            UserModel().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
