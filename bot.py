import praw
import urllib
import logging
from os import getenv
from time import sleep
from simplejson import load
from datetime import timedelta
from isodate import parse_duration
from logging.handlers import RotatingFileHandler 

class VideoHelper:
	def __init__(self, yt_api_key, logger):
		self.yt_api_key = yt_api_key
		self.logger = logger

	def get_site_and_id(self, url):
		parsed_url = urllib.parse.urlparse(url)
		if parsed_url.netloc.lower().endswith("youtube.com"):
			if parsed_url.path.split("/")[1] == "attribution_link":
				video_path = urllib.parse.urlparse(
					urllib.parse.parse_qs(parsed_url.query)["u"][0])
				return "yt", urllib.parse.parse_qs(video_path.query)["v"][0]
			return "yt", urllib.parse.parse_qs(parsed_url.query)["v"][0]
		elif parsed_url.netloc.lower().endswith("youtu.be"):
			return "yt", parsed_url.path.split("/")[1]
		elif parsed_url.netloc.lower().endswith("vimeo.com"):
			return "vim", parsed_url.path.split("/")[1]
		return None, None

	def get_duration(self, url):
		site, id = self.get_site_and_id(url)
		if site == "yt":
			return self.get_youtube_duration(id), id
		elif site == "vim":
			return self.get_vimeo_duration(id), id

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

		self.logger_level = logging.DEBUG
		self.logger = logging.getLogger(__name__)
		self.logger.setLevel(self.logger_level)
		self.ch = logging.StreamHandler()
		self.ch.setLevel(self.logger_level)
		self.formatter = logging.Formatter(
			"%(asctime)s - %(name)s - [%(levelname)s] - %(message)s")
		self.ch.setFormatter(self.formatter)
		self.logger.addHandler(self.ch)
		self.fh = RotatingFileHandler('bot.log', maxBytes=50000000)
		self.fh.setLevel(self.logger_level)
		self.fh.setFormatter(self.formatter)
		self.logger.addHandler(self.fh)

	def start(self):
		self.login()
		self.loop()

	def login(self):
		try:
			self.reddit = praw.Reddit()
			self.subreddit = self.reddit.subreddit(self.config["subreddit"])
			self.logger.info("Logged into reddit!")

			yt_api_key = self.reddit.config.custom.get("yt_api_key", getenv("yt_api_key"))
			if yt_api_key is None:
				raise TypeError("Make sure yt_api_key is in praw.ini or an envvar!")
			self.video_helper = VideoHelper(yt_api_key, self.logger)

		except Exception as ex:
			self.logger.critical(
				"An error occured while logging in: " + str(ex), exc_info=True)

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
							self.logger.exception(
								"Unexpected error occured " + 
								"while flairing post (" + post.id + 
								"): " + str(ex))
			except Exception as ex:
				self.logger.exception(
					"Unexpected error occured during loop: " + str(ex))
			sleep(self.config["retry_delay"])

	def is_flairable(self, post):
		if not (post.link_flair_text or 
			post.link_flair_css_class or
			post.is_self):
			return True
		return False

	def process_post(self, post):
		duration, id = self.video_helper.get_duration(post.url)

		if id is None:
			self.logger.info("Video ID not fetched, skipping: " + str(post))
			return
		if "duplicates" in self.config:
			# This *will* miss things due to reddit search being bad at 
			# the best of times. Specifically, it will probably miss
			# very close together duplicates as it takes upto an hour or
			# so for a post to show up in search results.
			# Ideally I should save all post IDs, vid IDs, timestamps
			# and just search through that, but this work every time 60%
			# of the time and I'm too lazy to do this properly.
			for result in self.subreddit.search("url:" + id, sort="new"):
				if result.id == post.id:
				    continue
				if post.created - result.created <= self.config["duplicates"]["time"]:
					if "remove" in self.config["duplicates"]:
						post.mod.remove()
						msg = post.reply(
							self.config["duplicates"]["remove"].format(
								"https://redd.it/" + result.id))
						msg.mod.distinguish("yes", sticky=True)
						self.logger.info("Removed " + post.id + 
							" as a recent duplicate of " + result.id + "!")
						if ("flair_text" in self.config["duplicates"] and
							"flair_class" in self.config["duplicates"]):
							self.flair_post(post, self.config["duplicates"])
						return
					elif "report" in self.config["duplicates"]:
						post.report(self.config["duplicates"]["report"])
						self.logger.info("Reported " + post.id + 
							" as a recent duplicate of " + result.id + "!")
						return	

		if duration is None:
			self.logger.info("Duration not fetched, skipping: " + str(post))
			return
		flair = self.get_duration_flair(duration)
		if flair is None:
			self.logger.info("No appropriate flair, skipping: " + str(post))
			return
		self.flair_post(post, flair)
		if "remove" in flair:
			post.mod.remove()
			self.logger.info("Removed: " + str(post) + ", " + str(duration))
			msg = post.reply(flair["remove"])
			msg.mod.distinguish("yes", sticky=True)
			self.logger.info("Sent Removal Message For: " + str(post))
		elif "report" in flair:
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
		for flair in self.config["flairs"]:
			if flair["range"][0] <= duration and flair["range"][1] >= duration:
				return flair

def load_config():
	config = None
	with open("config.json") as config_file:
		config = load(config_file)
	for flair in config["flairs"]:
		flair["range"][0] = parse_duration(flair["range"][0])
		flair["range"][1] = parse_duration(flair["range"][1])
	return config

def main():
	config = load_config()
	bot = Bot(config)
	bot.start()

if __name__ == "__main__":
	main()
