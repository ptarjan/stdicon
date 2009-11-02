from google.appengine.ext import webapp

register = webapp.template.create_template_register()


@register.filter
def elipses(value, limits):
    start, end = map(int, limits.split(","))
    if len(value) < start + end + 3:
        return value
    else:
        return value[:start] + "..." + value[-end:]
