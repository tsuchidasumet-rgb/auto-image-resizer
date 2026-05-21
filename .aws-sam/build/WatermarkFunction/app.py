import boto3
import io
import base64
from PIL import Image, ImageDraw, ImageFont

s3 = boto3.client('s3')
WEBSITE_NAME = 'Jibun Market'


def meta_decode(value):
    if not value:
        return ''
    try:
        return base64.b64decode(value.encode('ascii')).decode('utf-8')
    except Exception:
        return value


def load_font(size):
    try:
        return ImageFont.truetype('Montserrat-Bold.ttf', size)
    except Exception:
        return ImageFont.load_default()


def lambda_handler(event, context):
    try:
        bucket = event['Records'][0]['s3']['bucket']['name']
        key = event['Records'][0]['s3']['object']['key']

        response = s3.get_object(Bucket=bucket, Key=key)
        metadata = response.get('Metadata', {})
        user_name = metadata.get('username') or metadata.get('owner', 'ผู้ขาย')

        image_content = response['Body'].read()

        with Image.open(io.BytesIO(image_content)) as img:
            img = img.convert('RGBA')
            width, height = img.size

            txt_layer = Image.new('RGBA', img.size, (255, 255, 255, 0))
            draw = ImageDraw.Draw(txt_layer)

            font_site = load_font(max(14, int(width * 0.034)))
            font_seller = load_font(max(12, int(width * 0.028)))

            line_top = WEBSITE_NAME
            line_bottom = user_name

            bb1 = draw.textbbox((0, 0), line_top, font=font_site)
            bb2 = draw.textbbox((0, 0), line_bottom, font=font_seller)
            w1, h1 = bb1[2] - bb1[0], bb1[3] - bb1[1]
            w2, h2 = bb2[2] - bb2[0], bb2[3] - bb2[1]
            gap = max(4, int(height * 0.006))
            margin = int(width * 0.04)

            y_seller = height - margin - h2
            y_site = y_seller - gap - h1
            x_site = width - w1 - margin
            x_seller = width - w2 - margin

            shadow = (0, 0, 0, 100)
            for dx, dy in ((1, 1), (1, 0), (0, 1)):
                draw.text((x_site + dx, y_site + dy), line_top, fill=shadow, font=font_site)
                draw.text((x_seller + dx, y_seller + dy), line_bottom, fill=shadow, font=font_seller)

            draw.text((x_site, y_site), line_top, fill=(255, 255, 255, 200), font=font_site)
            draw.text((x_seller, y_seller), line_bottom, fill=(255, 255, 255, 170), font=font_seller)

            final_img = Image.alpha_composite(img, txt_layer).convert('RGB')
            buffer = io.BytesIO()
            final_img.save(buffer, format='JPEG', quality=85)
            buffer.seek(0)

            output_key = key.replace('uploads/', 'processed/')
            out_meta = {k: v for k, v in metadata.items() if v}
            if 'username' not in out_meta and user_name:
                out_meta['username'] = user_name
            if 'owner' not in out_meta and user_name:
                out_meta['owner'] = user_name

            s3.put_object(
                Bucket=bucket,
                Key=output_key,
                Body=buffer,
                ContentType='image/jpeg',
                Metadata=out_meta,
            )

        return {'statusCode': 200, 'body': 'Success'}

    except Exception as e:
        print(f'Error processing image: {e}')
        return {'statusCode': 500, 'body': str(e)}
