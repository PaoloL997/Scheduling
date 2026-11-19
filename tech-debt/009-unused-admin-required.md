# admin_required is unused and the README describes it as active

ui/permissions.py `admin_required`: no view uses it (the only references are its own module docstring and README.md "Access layers", which says the `write_required` / `admin_required` decorators wrap every mutating view of the web app and the tablet bridge, while the bridge uses `bridge_write_guard` in ui/views_bridge.py). Remove the decorator and correct the README sentence and the module docstring to name the guards really in use.
