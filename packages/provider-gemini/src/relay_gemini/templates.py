"""gemini's video and music templates: the galleries of the web app's video and music tools (XhaU0b `[4, null, 3]`
and `[4, [3], 3]`, read 25/10 on the AI Pro account). A template rides in the send's message as its id and its gallery
entry (`submit.request`); the provider sends the entry as gemini listed it. Pure data.

Video: "Doodle effects" is left out (it restyles an uploaded video; the relay takes images only). A template whose hint
asks for a photo ("Talking pets", "Meme me", ...) may be answered with a question when no image comes with it: a
refusal, refunded.
"""

# ponytail: copied from the gallery reads of 25/10. Google may rename or drop a template; a stale id is answered with
# a refusal (refunded). Read XhaU0b again and regenerate when the galleries change.
ASSET_BASE = "https://www.gstatic.com/bard-robin-zs/"     # every asset link of both galleries starts so

# name -> (template id, ((asset path, asset kind), ...)): kind 1 the card image, 2 the preview clip, 3 the preview song
VIDEO_TEMPLATES = {
    "Tiny world": ("75JE3AycmBL9jRT", (
        ("aura/omni/rect/new/tiny_world.webp", 1),
        ("aura/omni/webp/video/tiny_world.webp", 2))),
    "Anime": ("9eRTEvs6iyQXunM", (
        ("aura/omni/io_final_image/rect/anime.webp", 1),
        ("aura/omni/io_final/anime_2.webp", 2))),
    "8-bit adventure": ("FTPeApUVdPQkuVr", (
        ("aura/omni/io_final_image/rect/8bit_adventure_2.webp", 1),
        ("aura/omni/io_final/8bit_adventure_3.webp", 2))),
    "Logo reveal": ("7bDMoBdB2fwPpcf", (
        ("aura/omni/rect/new/logo_reveal.webp", 1),
        ("aura/omni/webp/video/logo_reveal.webp", 2))),
    "Walkthrough": ("3a5VNi9pufyqZqs", (
        ("aura/omni/rect/new/walkthough.webp", 1),
        ("aura/omni/webp/video/walkthough.webp", 2))),
    "Product showcase": ("3yf9ZqLxPTH7LgJ", (
        ("aura/omni/rect/new/product_showcase.webp", 1),
        ("aura/omni/webp/video/product_showcase.webp", 2))),
    "Plush world": ("4aWvRb73WmL5XBN", (
        ("aura/omni/webp/rect/plush_world.webp", 1),
        ("aura/omni/webp/video/plush_world.webp", 2))),
    "Talking pets": ("AhtAWUg2zWPUPgW", (
        ("aura/omni/io_final_image/rect/talking_pets.webp", 1),
        ("aura/omni/io_final/talking_pets_2.webp", 2))),
    "Storybook": ("Ri6F9ojAkdfyByK", (
        ("aura/omni/rect/new/storybook.webp", 1),
        ("aura/omni/webp/video/storybook.webp", 2))),
    "Birthday balloons": ("93s2bBQ5Dzt2dm9", (
        ("aura/omni/rect/new/birthday_balloons.webp", 1),
        ("aura/omni/webp/video/birthday_balloons.webp", 2))),
    "Pop-up card": ("6H4jCUAsk8LNLKU", (
        ("aura/omni/rect/new/popup_card.webp", 1),
        ("aura/omni/webp/video/popup_card.webp", 2))),
    "Party invite": ("6ocHqirXTSxq8aK", (
        ("aura/omni/io_final_image/rect/party_invite_1.webp", 1),
        ("aura/omni/io_final/party_invite_1.webp", 2))),
    "Origami": ("7GCGcGWQQtNjsQA", (
        ("aura/omni/io_final_image/rect/origami.webp", 1),
        ("aura/omni/io_final/origami_2.webp", 2))),
    "Pixelate": ("6Uo9qBWuEfVExfU", (
        ("aura/omni/io_final_image/rect/pixelate.webp", 1),
        ("aura/omni/io_final/pixelate_2.webp", 2))),
    "Edible city": ("4gpAyUtoRcmT8e5", (
        ("aura/omni/rect/new/edible_city.webp", 1),
        ("aura/omni/webp/video/edible_city.webp", 2))),
    "Chaotic vlog": ("BdEiJ8mUtzWcGH5", (
        ("aura/omni/webp/rect/chaotic_vlog.webp", 1),
        ("aura/omni/webp/video/chaotic_vlog.webp", 2))),
    "Video game": ("3kE4GJQCU5brKez", (
        ("aura/omni/io_final_image/rect/video_game_2.webp", 1),
        ("aura/omni/io_final/video_game_3.webp", 2))),
    "Anniversary": ("3XmnUZPnmCfb3hb", (
        ("aura/omni/rect/new/anniversary.webp", 1),
        ("aura/omni/webp/video/anniversary.webp", 2))),
    "Career day": ("3EGXxGS9yqNijRs", (
        ("aura/omni/webp/rect/career_day.webp", 1),
        ("aura/omni/webp/video/career_day.webp", 2))),
    "Cut paper": ("A6uNYsyMUNDYZPu", (
        ("aura/omni/io_final_image/rect/cut_paper.webp", 1),
        ("aura/omni/io_final/cut_paper_2.webp", 2))),
    "80's music video": ("5pELZJLkUBfC7Ph", (
        ("aura/omni/io_final_image/rect/80s_music_video.webp", 1),
        ("aura/omni/io_final/80s_music_video.webp", 2))),
    "Moon": ("4TtnDBeCMT6w7as", (
        ("aura/omni/io_final_image/rect/moon.webp", 1),
        ("aura/omni/io_final/moon.webp", 2))),
    "Analyze me": ("B3BEJHHW7k5qGMp", (
        ("aura/omni/io_final_image/rect/analyze_me.webp", 1),
        ("aura/omni/io_final/analyze_me_2.webp", 2))),
    "Indie pastel": ("8APV352ZmiWF6nA", (
        ("aura/omni/io_final_image/rect/indie_pastel.webp", 1),
        ("aura/omni/io_final/indie_pastel_3.webp", 2))),
    "Science concept": ("6ihsM9bZLDoKTfh", (
        ("aura/omni/webp/rect/science_concept.webp", 1),
        ("aura/omni/webp/video/science_concept.webp", 2))),
    "360 photo booth": ("AHZBxN7MoP3wmQ4", (
        ("aura/omni/webp/rect/360_photo_booth.webp", 1),
        ("aura/omni/webp/video/360_photo_booth.webp", 2))),
    "Comic book": ("ynnFJ43bmNLk4ww", (
        ("aura/omni/io_final_image/rect/comic_book_2.webp", 1),
        ("aura/omni/io_final/comic_book_3.webp", 2))),
    "Decades fashion": ("6DvdGfxetXaFC9h", (
        ("aura/omni/io_final_image/rect/decades_fashion.webp", 1),
        ("aura/omni/io_final/decades_fashion_2.webp", 2))),
    "Step into history": ("kFv8x4mtDXJMXKM", (
        ("aura/omni/webp/rect/step_into_history.webp", 1),
        ("aura/omni/webp/video/step_into_history.webp", 2))),
    "Montage": ("ZpbnUBPAXYmzgVe", (
        ("aura/omni/io_final_image/rect/montage.webp", 1),
        ("aura/omni/io_final/montage_2.webp", 2))),
    "Meme me": ("9jnq7b6EFUGhVKG", (
        ("aura/omni/io_final_image/rect/meme_me.webp", 1),
        ("aura/omni/io_final/meme_me_3.webp", 2))),
    "Noir": ("9t3uDGtYuf9noMN", (
        ("aura/omni/io_final_image/rect/noir.webp", 1),
        ("aura/omni/io_final/noir_2.webp", 2))),
}
MUSIC_TEMPLATES = {
    "Soundscape": ("8Ol4tYCe5eoAQEH", (
        ("musicgen/image_assets_v2/webp/soundscape_v2.webp", 1),
        ("musicgen/audio_assets_v2/soundscape.mp3", 3))),
    "Background music": ("3dBPOALU8oYWlnU", (
        ("musicgen/image_assets_v2/webp/background_music_v2.webp", 1),
        ("musicgen/audio_assets_v2/background_music.mp3", 3))),
    "Memorize something": ("rLjYnmMoWN9DZE4", (
        ("musicgen/image_assets_v2/webp/memorize_something_v2.webp", 1),
        ("musicgen/audio_assets_v2/memorize_something_v2.mp3", 3))),
    "Text messages": ("bfCrGH1hD35FRfn", (
        ("musicgen/image_assets_v2/webp/text_messages_v2.webp", 1),
        ("musicgen/audio_assets_v2/text_messages_v2.mp3", 3))),
    "Intro music": ("aezr34h1GrQVlfQ", (
        ("musicgen/image_assets_v2/webp/intro_music_v2.webp", 1),
        ("musicgen/audio_assets_v2/intro_music.mp3", 3))),
    "Montage": ("zzYUTkYTXWpqm1n", (
        ("musicgen/image_assets_v2/webp/montage_v2.webp", 1),
        ("musicgen/audio_assets_v2/montage.mp3", 3))),
    "Say it with a track": ("5GsumGp28g2qv5A", (
        ("musicgen/image_assets_v2/webp/say_it_with_a_track_v2.webp", 1),
        ("musicgen/audio_assets_v2/say_it_with_a_track.mp3", 3))),
    "Brand jingle": ("liHzmIzsPviIR8w", (
        ("musicgen/image_assets_v2/webp/brand_jingle_v2.webp", 1),
        ("musicgen/audio_assets_v2/brand_jingle.mp3", 3))),
    "Ringtone": ("gzTsgc1EsnJY3x7", (
        ("musicgen/image_assets_v2/webp/ringtone_v2.webp", 1),
        ("musicgen/audio_assets_v2/ringtone.mp3", 3))),
    "Streaming": ("AP3eS4LyQ0ABTF6", (
        ("musicgen/image_assets_v2/webp/streaming_v2.webp", 1),
        ("musicgen/audio_assets_v2/streaming.mp3", 3))),
    "Speech to song": ("1IOFSHlYFG3bM74", (
        ("musicgen/image_assets_v2/webp/speech_to_track_v2.webp", 1),
        ("musicgen/audio_assets_v2/speech_to_track.mp3", 3))),
    "Rap battle": ("C6upZiup36QqvIT", (
        ("musicgen/image_assets_v2/webp/rap_battle_v2.webp", 1),
        ("musicgen/audio_assets_v2/rap_battle.mp3", 3))),
    "Comedy": ("gK92mPxQ8bV4sLt", (
        ("musicgen/image_assets_v2/webp/comedy_v2.webp", 1),
        ("musicgen/audio_assets_v2/comedy_v2.mp3", 3))),
    "Team anthem": ("olAVI92BQeqjjXf", (
        ("musicgen/image_assets_v2/webp/team_anthem_v2.webp", 1),
        ("musicgen/audio_assets_v2/team_anthem.mp3", 3))),
    "Sports recap": ("KiN0UPllK4Vv9Lr", (
        ("musicgen/image_assets_v2/webp/sports_recap_v3.webp", 1),
        ("musicgen/audio_assets_v2/sports_recap.mp3", 3))),
    "Party invite": ("aaQiSIswj0Ewnr4", (
        ("musicgen/image_assets_v2/webp/party_invite_v2.webp", 1),
        ("musicgen/audio_assets_v2/party_invite.mp3", 3))),
    "Happy birthday": ("8VLzxfY55K7ShaE", (
        ("musicgen/image_assets_v2/webp/happy_birthday_v2.webp", 1),
        ("musicgen/audio_assets_v2/happy_birthday_v2.mp3", 3))),
    "Friend appreciation": ("nruKXwAlbUdKwfH", (
        ("musicgen/image_assets_v2/webp/friend_appreciation_v3.webp", 1),
        ("musicgen/audio_assets_v2/friend_appreciation.mp3", 3))),
    "Love story": ("Wz41oaXjIVlLeWB", (
        ("musicgen/image_assets_v2/webp/love_story_v2.webp", 1),
        ("musicgen/audio_assets_v2/love_story.mp3", 3))),
    "Birthday roast": ("wquXWfp0QTRnUKA", (
        ("musicgen/image_assets_v2/webp/birthday_roast_v2.webp", 1),
        ("musicgen/audio_assets_v2/birthday_roast.mp3", 3))),
    "Ode to your pet": ("iatEHmaC6yjiM32", (
        ("musicgen/image_assets_v2/webp/ode_to_your_pet_v2.webp", 1),
        ("musicgen/audio_assets_v2/ode_to_your_pet.mp3", 3))),
    "Hometown tribute": ("zlO1RVxXghVIwwy", (
        ("musicgen/image_assets_v2/webp/hometown_tribute_v2.webp", 1),
        ("musicgen/audio_assets_v2/hometown_tribute.mp3", 3))),
    "Weather report": ("fKf5G7E18Jw6TkT", (
        ("musicgen/image_assets_v2/webp/weather_report_v2.webp", 1),
        ("musicgen/audio_assets_v2/weather_report_v2.mp3", 3))),
    "Business promo": ("fYM4aUBDry1PX8c", (
        ("musicgen/image_assets_v2/webp/business_promo_v3.webp", 1),
        ("musicgen/audio_assets_v2/business_promo.mp3", 3))),
}
