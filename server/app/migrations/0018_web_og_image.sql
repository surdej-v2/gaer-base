-- og:image per page on the public website (/w): an upload in Object Storage that replaces the generated image.
-- The images for the other anonymous pages (/w/ front, /login, /privatliv, default) are in settings.og_images.
alter table web_pages add column og_image_key text;
