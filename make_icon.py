from PIL import Image, ImageDraw, ImageFont

size = 256
img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 8, size - 8, size - 8), radius=48, fill=(31, 106, 165, 255))
font = ImageFont.truetype("arialbd.ttf", 150)
d.text((size / 2, size / 2), "K", font=font, fill="white", anchor="mm")
img.save("app.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])