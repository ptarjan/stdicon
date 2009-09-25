# Paul Tarjan : http://paulisageek.com

import os
import mimetypes
import logging
import simplejson
import random
from urllib import unquote

import wsgiref.handlers

from google.appengine.api import users
from google.appengine.api import images
from google.appengine.api import memcache
from google.appengine.api import urlfetch

from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template

from datetime import timedelta, datetime

mimetypes.knownfiles.append("mime.types")
# load the mimetypes from the file (this is cached between requests)
mimetypes.add_type("text/plain", ".txt")
mimetypes.init(mimetypes.knownfiles)

template.register_template_library('filters')

class Set(db.Model):
    name = db.StringProperty(required=True)
    url = db.LinkProperty()
    modified = db.DateTimeProperty(auto_now=True)
    created = db.DateTimeProperty(auto_now_add=True)
    
class Icon(db.Model):
    mimetype = db.StringProperty(required=True)
    set = db.ReferenceProperty(Set, required=True)
    contents = db.BlobProperty()
    modified = db.DateTimeProperty(auto_now=True)
    created = db.DateTimeProperty(auto_now_add=True)
   
    @staticmethod
    def create(mimetype, contents, setname) : 
        logging.info(str((mimetype, setname)))
        mimetype = mimetype.strip()
        guess, encoding = mimetypes.guess_type("dummy." + mimetype)
        if guess :
            logging.info("Guessed '%s' for '%s'" % (guess, mimetype))
            mimetype = guess

        icon = None
        if mimetype and setname and contents :
            set = Set.all().filter("name =", setname).get()

            # don't post duplicates
            icon = Icon.all().filter("set =", set).filter("mimetype =", mimetype).get()
            if not icon :
                icon = Icon(mimetype=mimetype, set=set)
                icon.contents = contents
                icon.put()

        return icon

class IndexHandler(webapp.RequestHandler):
    def get(self):
        template_values = {}
        sets = Set.all().order("name")

        iconsets = []
        for set in sets :
            cached = memcache.get("iconexample_set_" + set.name)
            if cached : 
                iconset = cached
            else :
                query = Icon.all().filter("set = ", set)
                max = query.count()
                if max == 0 :
                    icon = None
                else :  
                    ind = int(random.random() * max)
                    icon = query[ind]
                iconset ={"set" : set, "icon" : icon}
                if icon :
                    memcache.set("iconexample_set_" + set.name, iconset, 60 * 60 * 24) # 1 day

            iconsets.append(iconset)

        template_values['sets'] = iconsets

        path = os.path.join(os.path.dirname(__file__), 'index.html')
        self.response.out.write(template.render(path, template_values))

class SetHandler(webapp.RequestHandler):
    def get(self, setname):
        try :
            set = Set.all().filter("name =", setname).get()
        except Exception, why :
            logging.error(why)
            set = None

        if not set :
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.set_status(404)
            self.response.out.write("Set '%s' not found" % (setname))
            return False
            
        template_values = {}
        template_values['set'] = set
        template_values['icons'] = Icon.all().filter("set =", set).order("mimetype")

        path = os.path.join(os.path.dirname(__file__), 'icon_list.html')
        self.response.out.write(template.render(path, template_values))

class IconHandler(webapp.RequestHandler):
    def error(self, status, msg="") :
        default = self.request.get("default")
        if not default :
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.set_status(status)
            self.response.out.write(msg)
        else :
            try :
                image = None
                # image = memcache.get("default_image_" + default)
                if not image :
                    image = urlfetch.fetch(default).content
                    try :
                        memcache.set("default_image_" + default, image, 60 * 60) # 1 hour
                    except Exception, why :
                        logging.error(why)

                return self.image(image, cache=False)

            except urlfetch.Error, why :
                self.response.headers['Content-Type'] = 'text/plain'
                self.response.set_status(404)
                self.response.out.write("Default image error: %s : %s" % (default, why))

    def get(self, default_set="crystal"):
        image = memcache.get("image_" + self.request.url)
        if image : 
            return self.respond_image(image)

        # path is urlencoded with + being %2B
        path = unquote(self.request.path)

        parts = path.split("/")[1:]
        if len(parts) == 1 :
            setname = default_set
            mimetype = parts[0]
        else :
            setname = parts[0]
            mimetype = "/".join(parts[1:])

        try :
            set = Set.all().filter("name =", setname).get()
        except Exception, why :
            logging.error(why)
            set = None

        if not set :
            setname = default_set
            set = Set.all().filter("name = ", setname).get()
            mimetype = "/".join(parts)

        guess, encoding = mimetypes.guess_type("dummy." + mimetype)
        if guess :
            logging.info("Guessed '%s' for '%s'" % (guess, mimetype))
            mimetype = guess

        try :
            icon = Icon.all().filter("set =", set).filter("mimetype =", mimetype).get()
        except Exception, why :
            logging.error(why)
            icon = None

        if not icon :
            parts = mimetype.split("/")
            if len(parts) >= 1 :
                generic_mimetype = parts[0] + "/x-generic"
                try :
                    icon = Icon.all().filter("set =", set).filter("mimetype =", generic_mimetype).get()
                except Exception, why :
                    logging.error(why)

            if not icon :
                return self.error(404, "Icon '%s' not found in '%s' set" % (mimetype, setname))

        if not icon.contents :
            return self.error(500, "'%s' from '%s' has 0 bytes" % (mimetype, setname))

        return self.image(icon.contents)

    def image(self, contents, cache=True) :
            
        size = self.request.get("size")
        if size :
            boom = size.split("x")
            if len(boom) == 1:
                h = size
                w = size
            else :
                h = boom[0]
                w = boom[1]

            w = min(int(w), 256)
            h = min(int(h), 256)
            
            try :
                image = images.resize(contents, w, h)
            except ValueError, why :
                image = contents

            if cache :
                try :
                    memcache.set("image_" + self.request.url, image, 60 * 60 * 24) # 1 day
                except Exception, why :
                    logging.error(why)

        else :
            image = contents

        return self.respond_image(image)

    def respond_image(self, image) :
        self.response.headers['Content-Type'] = 'image/png'
        hours = 24*7
        then = timedelta(hours=hours) + datetime.now()
        self.response.headers['Expires'] = then.strftime("%a, %d %b %Y %H:%M:%S GMT")
        self.response.headers['Cache-Control'] = 'max-age=%d' % int(3600*hours)
        self.response.out.write(image)
        return True

    def post(self) :
        return get()

class FaviconHandler(IconHandler):
    def get(self):
        icon = Icon.all().filter("mimetype =", "text/html").get()
        return self.respond_image(icon.contents)

class CreateHandler(webapp.RequestHandler):
    def get(self):
        user = users.get_current_user()
        if not user :
            return self.redirect(users.create_login_url(self.request.url))
            
        if not users.is_current_user_admin() :
            logging.warning("Non-admin found the create url : %s", user)
            return self.redirect("/")

        template_values = {}
        template_values['sets'] = Set.all().order("name")

        path = os.path.join(os.path.dirname(__file__), 'create.html')
        self.response.out.write(template.render(path, template_values))

    def post(self) :
        if not users.is_current_user_admin() :
            return self.redirect("/")

        setname = self.request.get("setname")
        url = self.request.get("url")
        if setname :
            Set(name=setname, url=url).put()

        return self.get()

class CreateIconHandler(webapp.RequestHandler):
    def get(self, setname):
        if not users.is_current_user_admin() :
            return self.redirect("/")

        set = Set.all().filter("name =", setname).get()
        if not set :
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.set_status(404)
            self.response.out.write("Set '%s' not found" % (setname))
            return False
            
        template_values = {}
        template_values['set'] = set
        template_values['icons'] = Icon.all().filter("set =", set).order("mimetype")

        path = os.path.join(os.path.dirname(__file__), 'create_icon.html')
        self.response.out.write(template.render(path, template_values))

    def post(self, setname) :
        if not users.is_current_user_admin() :
            return self.redirect("/")
        
        mimetype = self.request.get("mimetype")
        setname = self.request.get("set")
        contents = self.request.get("contents")
        icon = Icon.create(mimetype, contents, setname)

        return self.get(setname)

class CreateIconZipHandler(webapp.RequestHandler):
    def post(self, setname) :
        if not users.is_current_user_admin() :
            return self.redirect("/")

        import zipfile
        from StringIO import StringIO
        import re
        zip = zipfile.ZipFile(StringIO(self.request.get("contents")))
        setname = self.request.get("set")

        for name in zip.namelist() :
            match = re.search("-mime-(.*?)[.]", name)
            if not match : continue

            mimetype = match.groups()[0].replace("-", "/")
            contents = zip.read(name)
            
            icon = Icon.create(mimetype, contents, setname)

        return self.redirect("/create/" + setname)

class MimetypesHandler(webapp.RequestHandler):
    def get(self) :
        keys = mimetypes.types_map.keys()
        keys.sort()
        map = []
        for k in keys :
            map.append({k: mimetypes.types_map[k]})
        if self.request.get("format") != "xml" :
            self.response.headers['Content-Type'] = 'application/json'
            self.response.out.write(simplejson.dumps(map))
        else :
            self.response.headers['Content-Type'] = 'application/xml'
            o = self.response.out
            o.write("<mimetypes>")
            for f in map :
                k = f.keys()[0]
                v = f[k]
                o.write("<file><ext>%s</ext><mimetype>%s</mimetype></file>" % (k,v))
            o.write("</mimetypes>")

class MimetypeLookupHandler(webapp.RequestHandler):
    def get(self, method, type) :
        output = ""
        if method == "ext" or method == "extension" :
            guess, handler = mimetypes.guess_type("dummy." + type)
            if guess :
                output = guess
            else :
                self.response.set_status(404)
                output = "Extension '%s' has no known mimetype" % (type)
        elif method == "mimetype" :
            ext = mimetypes.guess_extension(type)
            if ext and ext[0] == "." :
                output = ext[1:]
            else :
                self.response.set_status(404)
                output = "Mimetype '%s' has no known extension" % (type)

        self.response.headers['Content-Type'] = 'text/plain'
        if self.request.get("callback") :
            self.response.headers['Content-Type'] = 'application/javascript'
            self.response.out.write(self.request.get("callback") + "(" + simplejson.dumps(output) + ")")
        else :
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.out.write(output)
            

           
class FixHandler(webapp.RequestHandler):
    def get(self) :
        set = Set.all().filter("name =", "crystal").get()
        set.url = "http://www.everaldo.com/crystal/"
        set.put()
        set = Set.all().filter("name =", "silk").get()
        set.url = "http://www.famfamfam.com/lab/icons/silk/"
        set.put()
        set = Set.all().filter("name =", "tango").get()
        set.url = "http://tango.freedesktop.org/Tango_Icon_Library"
        set.put()
        set = Set.all().filter("name =", "gnome").get()
        set.url = "http://art.gnome.org/themes/icon/1100"
        set.put()
        set = Set.all().filter("name =", "apache").get()
        set.url = "http://httpd.apache.org/"
        set.put()
            
def main():
  application = webapp.WSGIApplication([
                                        (r'/', IndexHandler),
                                        (r'/favicon.ico', FaviconHandler),

                                        # admin
                                        (r'/create/?', CreateHandler),
                                        (r'/create/(.+)/zip?', CreateIconZipHandler),
                                        (r'/create/(.+)/?', CreateIconHandler),
                                        (r'/fix', FixHandler),

                                        (r'/mimetypes', MimetypesHandler),
                                        (r'/(ext|extension|mimetype)/(.+)', MimetypeLookupHandler),
                                        (r'/(.+)/', SetHandler),
                                        (r'/.+', IconHandler),
                                       ],
                                       debug=True)
  wsgiref.handlers.CGIHandler().run(application)

if __name__ == '__main__':
  main()
