import praw
import urllib
import logging
from time import sleep
from simplejson import load
from datetime import timedelta
from isodate import parse_duration

class VideoHelper:
	def __init__(self, config, logger):
		self.config = config
		self.yt_api_key = config["auth"]["youtube"]["api_key"]

		self.logger = logger

	def get_site_and_id(self, url):
		parsed_url = urllib.parse.urlparse(url)
		if parsed_url.netloc.lower().endswith("youtube.com"):
			return "yt", urllib.parse.parse_qs(parsed_url.query)["v"][0]
		elif parsed_url.netloc.lower().endswith("youtu.be"):
			return "yt", parsed_url.path.split("/")[1]
		elif parsed_url.netloc.lower().endswith("vimeo.com"):
			return "vim", parsed_url.path.split("/")[1]

	def get_duration(self, url):
		site, id = self.get_site_and_id(url)
		if site is "yt":
			return self.get_youtube_duration(id)
		elif site is "vim":
			return self.get_vimeo_duration(id)

	def get_vimeo_duration(self, id):
		self.logger.info("Fetching Vimeo duration for: " + id)
		url = "https://vimeo.com/api/v2/video/" + id + ".json"
		try:
			response = urllib.request.urlopen(url)
			data = load(response)
			return timedelta(0, data[0]["duration"])
		except Exception as ex:
			self.logger.warning("Unexpected response from Vimeo: " + str(ex))

	def get_youtube_duration(self, id):
		self.logger.info("Fetching YT duration for: " + id)
		url = "https://www.googleapis.com/youtube/v3/videos"
		values = {
			"key" : self.yt_api_key,
			"id" : id,
			"part" : "contentDetails"
		}
		params = urllib.parse.urlencode(values)
		try:
			response = urllib.request.urlopen(url + "?" + params)
			data = load(response)
			if data["items"]:
				duration = data["items"][0]["contentDetails"]["duration"]
				self.logger.info("Retrieved duration: " + 
					id + " " + duration)
				return parse_duration(duration)
			else:
				self.logger.warning(
					"Incomplete response from YT, video might not exist!")
		except Exception as ex:
			self.logger.warning(
				"Unexpected response from YT: " + str(ex))

class Bot:
	def __init__(self, config):
		self.config = config
		self.auth_reddit = self.config["auth"]["reddit"]


		self.logger = logging.getLogger()
		self.logger.setLevel(logging.INFO)
		ch = logging.StreamHandler()
		ch.setLevel(logging.INFO)
		formatter = logging.Formatter(
			"%(asctime)s - %(name)s - [%(levelname)s] - %(message)s")
		ch.setFormatter(formatter)
		self.logger.addHandler(ch)

		self.video_helper = VideoHelper(self.config, self.logger)

	def start(self):
		self.login()
		self.loop()

	def login(self):
		try:
			self.reddit = praw.Reddit(
				client_id = self.auth_reddit["client_id"],
				client_secret = self.auth_reddit["client_secret"],
				user_agent = self.auth_reddit["user_agent"],
				username = self.auth_reddit["username"],
				password = self.auth_reddit["password"])
			self.subreddit = self.reddit.subreddit(self.config["subreddit"])
			self.logger.info("Logged in!")
		except Exception as ex:
			self.logger.critical(
				"An error occured while logging in: " + str(ex))

	def loop(self):
		while True:
			try:
				self.logger.info("Starting submissions stream!")
				for post in self.subreddit.stream.submissions():
					if self.is_flairable(post):
						try:
							self.logger.info("New unflaired post: " + str(post))
							self.process_post(post)
						except Exception as ex:
							self.logger.error(
								"Unexpected error occured " + 
								" while flairing post: "  + str(ex))
			except Exception as ex:
				self.logger.error(
					"Unexpected error occured during loop: " + str(ex))
			sleep(self.config["retry_delay"])

	def is_flairable(self, post):
		if not (post.link_flair_text or 
			post.link_flair_css_class or
			post.is_self):
			return True
		return False

	def process_post(self, post):
		duration = self.video_helper.get_duration(post.url)
		if duration is None:
			self.logger.info("Duration not fetched, skipping: " + str(post))
			return
		flair = self.get_duration_flair(duration)
		if flair is None:
			self.logger.info("No appropriate flair, skipping: " + str(post))
			return
		self.flair_post(post, flair)
		if "report" in flair:
			post.report(flair["report"])
			self.logger.info("Reported " + str(post) + 
				" for '" + flair["report"] + "'")


	def flair_post(self, post, flair):
		post.mod.flair(
			text=flair["flair_text"], 
			css_class=flair["flair_class"])
		self.logger.info(
			"Flaired " + str(post) + " with text '" + 
			flair["flair_text"] + "' and class '" + flair["flair_class"] + "'")

	def get_duration_flair(self, duration):
		for flair in self.config["actions"]:
			if flair["range"][0] <= duration and flair["range"][1] >= duration:
				return flair

def load_config():
	config = None
	with open("config.json") as config_file:
		config = load(config_file)
	for flair in config["actions"]:
		flair["range"][0] = parse_duration(flair["range"][0])
		flair["range"][1] = parse_duration(flair["range"][1])
	return config

def main():
	config = load_config()
	bot = Bot(config)
	bot.start()

if __name__ == "__main__":
	main()