"""Finder layout for RemoteAgent's drag-to-install disk image."""
import os

format = "UDZO"
files = [os.environ["REMOTE_AGENT_DMG_APP"]]
symlinks = {"Applications": "/Applications"}
background = os.environ["REMOTE_AGENT_DMG_BACKGROUND"]
window_rect = ((200, 150), (660, 420))
icon_locations = {"RemoteAgent.app": (165, 190), "Applications": (495, 190)}
icon_size = 112
text_size = 14
default_view = "icon-view"
show_toolbar = False
show_status_bar = False
show_tab_view = False
show_pathbar = False
show_sidebar = False
include_icon_view_settings = True
include_list_view_settings = False
arrange_by = None
