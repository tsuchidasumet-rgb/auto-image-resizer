import json
import os
import base64
import uuid
from datetime import datetime, timezone
from botocore.exceptions import ClientError
import boto3

s3 = boto3.client('s3', region_name='ap-northeast-1')
cognito = boto3.client('cognito-idp', region_name='ap-northeast-1')
dynamodb = boto3.resource('dynamodb', region_name='ap-northeast-1')

BUCKET = os.environ['BUCKET_NAME']
USER_POOL_CLIENT_ID = os.environ['USER_POOL_CLIENT_ID']
PROFILES_TABLE = os.environ.get('PROFILES_TABLE', '')
ORDERS_TABLE = os.environ.get('ORDERS_TABLE', '')


def meta_encode(value):
    if not value:
        return ''
    return base64.b64encode(value.encode('utf-8')).decode('ascii')


def meta_decode(value):
    if not value:
        return ''
    try:
        return base64.b64decode(value.encode('ascii')).decode('utf-8')
    except Exception:
        return value


def get_headers(event):
    raw = event.get('headers') or {}
    return {k.lower(): v for k, v in raw.items()}


def get_access_token(event):
    auth = get_headers(event).get('authorization', '')
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    return auth.strip()


def get_cognito_user(access_token):
    user = cognito.get_user(AccessToken=access_token)
    attrs = {a['Name']: a['Value'] for a in user.get('UserAttributes', [])}
    email = attrs.get('email', '').strip().lower()
    if not email:
        raise cognito.exceptions.NotAuthorizedException()
    return email, attrs


def profiles_table():
    if not PROFILES_TABLE:
        return None
    return dynamodb.Table(PROFILES_TABLE)


def orders_table():
    if not ORDERS_TABLE:
        return None
    return dynamodb.Table(ORDERS_TABLE)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def lambda_handler(event, context):
    method = event.get('requestContext', {}).get('http', {}).get('method', '')
    path = event.get('rawPath', '')

    routes = {
        ('POST', '/auth/register'): handle_register,
        ('POST', '/auth/login'): handle_login,
        ('POST', '/auth/confirm'): handle_confirm,
        ('POST', '/upload'): handle_upload,
        ('GET', '/images'): handle_list_images,
        ('POST', '/images/delete'): handle_delete_image,
        ('GET', '/me/profile'): handle_get_profile,
        ('PUT', '/me/profile'): handle_put_profile,
        ('POST', '/me/avatar'): handle_avatar_upload,
        ('GET', '/me/orders/buying'): handle_orders_buying,
        ('GET', '/me/orders/selling'): handle_orders_selling,
        ('POST', '/orders/buy'): handle_buy,
    }

    handler = routes.get((method, path))
    if handler:
        return handler(event)
    return response(404, {'error': 'Not found'})


def handle_register(event):
    try:
        body = json.loads(event.get('body', '{}'))
        email = body['email'].strip().lower()
        password = body['password']

        cognito.sign_up(
            ClientId=USER_POOL_CLIENT_ID,
            Username=email,
            Password=password,
            UserAttributes=[
                {'Name': 'email', 'Value': email},
                {'Name': 'nickname', 'Value': email},
            ],
        )
        return response(200, {'message': 'สมัครสมาชิกสำเร็จ กรุณาตรวจสอบอีเมลเพื่อยืนยัน'})
    except cognito.exceptions.UsernameExistsException:
        return response(400, {'error': 'อีเมลนี้มีผู้ใช้งานแล้ว'})
    except Exception as e:
        return response(500, {'error': str(e)})


def handle_confirm(event):
    try:
        body = json.loads(event.get('body', '{}'))
        cognito.confirm_sign_up(
            ClientId=USER_POOL_CLIENT_ID,
            Username=body['email'].strip().lower(),
            ConfirmationCode=body['code'],
        )
        return response(200, {'message': 'ยืนยันอีเมลสำเร็จ'})
    except Exception as e:
        return response(400, {'error': str(e)})


def handle_login(event):
    try:
        body = json.loads(event.get('body', '{}'))
        result = cognito.initiate_auth(
            ClientId=USER_POOL_CLIENT_ID,
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters={
                'USERNAME': body['email'].strip().lower(),
                'PASSWORD': body['password'],
            },
        )
        tokens = result['AuthenticationResult']
        return response(200, {
            'accessToken': tokens['AccessToken'],
            'refreshToken': tokens['RefreshToken'],
            'expiresIn': tokens['ExpiresIn'],
            'email': body['email'].strip().lower(),
        })
    except cognito.exceptions.NotAuthorizedException:
        return response(401, {'error': 'อีเมลหรือรหัสผ่านไม่ถูกต้อง'})
    except cognito.exceptions.UserNotConfirmedException:
        return response(403, {'error': 'กรุณายืนยันอีเมลก่อนเข้าสู่ระบบ'})
    except Exception as e:
        return response(500, {'error': str(e)})


def handle_upload(event):
    try:
        access_token = get_access_token(event)
        email, _ = get_cognito_user(access_token)

        body = json.loads(event.get('body', '{}'))
        title = (body.get('title') or 'สินค้า').strip()[:120]
        description = (body.get('description') or '').strip()[:500]
        category = (body.get('category') or 'อื่นๆ').strip()[:40]
        price = str(body.get('price') or '').strip()[:12]
        product_id = (body.get('productId') or str(uuid.uuid4())).strip()[:64]

        files = body.get('files')
        if not files:
            files = [{
                'filename': body.get('filename', 'image.jpg'),
                'contentType': body.get('contentType', 'image/jpeg'),
            }]

        base_metadata = {
            'username': email,
            'owner': email,
            'productid': product_id,
            'title': meta_encode(title),
            'description': meta_encode(description),
            'category': meta_encode(category),
            'price': price,
        }

        uploads = []
        for index, file_info in enumerate(files):
            filename = file_info.get('filename', f'image_{index}.jpg')
            content_type = file_info.get('contentType', 'image/jpeg')
            key = f"uploads/{product_id}/{filename}"
            meta = {**base_metadata, 'imageindex': str(index)}

            presigned_url = s3.generate_presigned_url(
                'put_object',
                Params={
                    'Bucket': BUCKET,
                    'Key': key,
                    'ContentType': content_type,
                    'Metadata': meta,
                },
                ExpiresIn=300,
            )
            processed_key = key.replace('uploads/', 'processed/')
            uploads.append({
                'uploadUrl': presigned_url,
                'key': key,
                'processedKey': processed_key,
                'metaHeaders': {f'x-amz-meta-{k}': v for k, v in meta.items()},
            })

        payload = {
            'productId': product_id,
            'sellerName': email,
            'uploads': uploads,
        }
        if len(uploads) == 1:
            payload.update(uploads[0])
        return response(200, payload)
    except cognito.exceptions.NotAuthorizedException:
        return response(401, {'error': 'Token หมดอายุ กรุณา login ใหม่'})
    except Exception as e:
        return response(500, {'error': str(e)})


def image_from_s3_object(obj):
    key = obj['Key']
    if key.endswith('/'):
        return None
    meta = {}
    try:
        head = s3.head_object(Bucket=BUCKET, Key=key)
        meta = head.get('Metadata', {})
    except ClientError:
        pass

    url = s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': BUCKET, 'Key': key},
        ExpiresIn=3600,
    )
    owner = meta.get('owner') or meta.get('username', '')
    return {
        'key': key,
        'url': url,
        'size': obj['Size'],
        'lastModified': obj['LastModified'].isoformat(),
        'seller': owner or 'ผู้ขาย',
        'owner': owner,
        'title': meta_decode(meta.get('title', '')) or 'สินค้า',
        'description': meta_decode(meta.get('description', '')),
        'category': meta_decode(meta.get('category', '')) or 'อื่นๆ',
        'price': meta.get('price', ''),
        'productId': meta.get('productid', ''),
        'imageIndex': int(meta.get('imageindex', '0') or 0),
    }


def handle_list_images(event):
    try:
        result = s3.list_objects_v2(Bucket=BUCKET, Prefix='processed/')
        images = []
        for obj in result.get('Contents', []):
            item = image_from_s3_object(obj)
            if item:
                images.append(item)
        images.sort(key=lambda x: (x.get('productId', ''), x.get('imageIndex', 0), x['lastModified']), reverse=True)
        return response(200, {'images': images})
    except ClientError as e:
        return response(500, {'error': e.response['Error'].get('Message', str(e))})
    except Exception as e:
        return response(500, {'error': str(e)})


def handle_delete_image(event):
    try:
        access_token = get_access_token(event)
        email, _ = get_cognito_user(access_token)

        body = json.loads(event.get('body', '{}'))
        key = body.get('key', '').strip()
        product_id = body.get('productId', '').strip()

        if product_id and not key:
            prefix = f"processed/{product_id}/"
            result = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
            deleted = 0
            for obj in result.get('Contents', []):
                obj_key = obj['Key']
                head = s3.head_object(Bucket=BUCKET, Key=obj_key)
                owner = head.get('Metadata', {}).get('owner', '')
                if owner != email:
                    return response(403, {'error': 'ลบได้เฉพาะสินค้าของตัวเอง'})
                s3.delete_object(Bucket=BUCKET, Key=obj_key)
                upload_key = obj_key.replace('processed/', 'uploads/', 1)
                try:
                    s3.delete_object(Bucket=BUCKET, Key=upload_key)
                except ClientError:
                    pass
                deleted += 1
            return response(200, {'message': f'ลบสำเร็จ {deleted} รูป'})

        if not key.startswith('processed/') or '..' in key:
            return response(400, {'error': 'key ไม่ถูกต้อง'})

        head = s3.head_object(Bucket=BUCKET, Key=key)
        owner = head.get('Metadata', {}).get('owner', head.get('Metadata', {}).get('username', ''))
        if owner != email:
            return response(403, {'error': 'ลบได้เฉพาะสินค้าของตัวเอง'})

        s3.delete_object(Bucket=BUCKET, Key=key)
        upload_key = key.replace('processed/', 'uploads/', 1)
        try:
            s3.delete_object(Bucket=BUCKET, Key=upload_key)
        except ClientError:
            pass

        return response(200, {'message': 'ลบสำเร็จ'})
    except cognito.exceptions.NotAuthorizedException:
        return response(401, {'error': 'Token หมดอายุ กรุณา login ใหม่'})
    except ClientError as e:
        return response(500, {'error': e.response['Error'].get('Message', str(e))})
    except Exception as e:
        return response(500, {'error': str(e)})


def default_profile(email):
    return {
        'email': email,
        'firstName': '',
        'lastName': '',
        'birthDate': '',
        'address': '',
        'phone': '',
        'avatarUrl': '',
        'updatedAt': utc_now(),
    }


def handle_get_profile(event):
    try:
        access_token = get_access_token(event)
        email, _ = get_cognito_user(access_token)
        profile = default_profile(email)
        table = profiles_table()
        if table:
            item = table.get_item(Key={'email': email}).get('Item')
            if item:
                profile.update(item)
                if profile.get('avatarKey'):
                    profile['avatarUrl'] = s3.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': BUCKET, 'Key': profile['avatarKey']},
                        ExpiresIn=3600,
                    )
        return response(200, {'profile': profile})
    except cognito.exceptions.NotAuthorizedException:
        return response(401, {'error': 'Token หมดอายุ'})
    except Exception as e:
        return response(500, {'error': str(e)})


def handle_put_profile(event):
    try:
        access_token = get_access_token(event)
        email, _ = get_cognito_user(access_token)
        body = json.loads(event.get('body', '{}'))
        table = profiles_table()
        if not table:
            return response(500, {'error': 'ระบบโปรไฟล์ยังไม่พร้อม deploy'})

        item = {
            'email': email,
            'firstName': (body.get('firstName') or '')[:80],
            'lastName': (body.get('lastName') or '')[:80],
            'birthDate': (body.get('birthDate') or '')[:20],
            'address': (body.get('address') or '')[:300],
            'phone': (body.get('phone') or '')[:30],
            'updatedAt': utc_now(),
        }
        existing = table.get_item(Key={'email': email}).get('Item', {})
        if existing.get('avatarKey'):
            item['avatarKey'] = existing['avatarKey']
        table.put_item(Item=item)
        profile = default_profile(email)
        profile.update(item)
        return response(200, {'profile': profile, 'message': 'บันทึกข้อมูลแล้ว'})
    except cognito.exceptions.NotAuthorizedException:
        return response(401, {'error': 'Token หมดอายุ'})
    except Exception as e:
        return response(500, {'error': str(e)})


def handle_avatar_upload(event):
    try:
        access_token = get_access_token(event)
        email, _ = get_cognito_user(access_token)
        body = json.loads(event.get('body', '{}'))
        content_type = body.get('contentType', 'image/jpeg')
        avatar_key = f"profiles/{email}/avatar.jpg"

        url = s3.generate_presigned_url(
            'put_object',
            Params={'Bucket': BUCKET, 'Key': avatar_key, 'ContentType': content_type},
            ExpiresIn=300,
        )
        table = profiles_table()
        if table:
            existing = table.get_item(Key={'email': email}).get('Item', default_profile(email))
            existing['avatarKey'] = avatar_key
            existing['updatedAt'] = utc_now()
            table.put_item(Item=existing)

        return response(200, {'uploadUrl': url, 'avatarKey': avatar_key})
    except cognito.exceptions.NotAuthorizedException:
        return response(401, {'error': 'Token หมดอายุ'})
    except Exception as e:
        return response(500, {'error': str(e)})


def handle_buy(event):
    try:
        access_token = get_access_token(event)
        buyer_email, _ = get_cognito_user(access_token)
        body = json.loads(event.get('body', '{}'))
        product_id = (body.get('productId') or '').strip()
        if not product_id:
            return response(400, {'error': 'ต้องระบุ productId'})

        prefix = f"processed/{product_id}/"
        result = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix, MaxKeys=1)
        contents = result.get('Contents', [])
        if not contents:
            return response(404, {'error': 'ไม่พบสินค้า'})

        sample = image_from_s3_object(contents[0])
        seller_email = sample.get('owner', '')
        if seller_email == buyer_email:
            return response(400, {'error': 'ไม่สามารถซื้อสินค้าของตัวเองได้'})

        table = orders_table()
        if not table:
            return response(500, {'error': 'ระบบสั่งซื้อยังไม่พร้อม deploy'})

        order_id = str(uuid.uuid4())
        order = {
            'orderId': order_id,
            'productId': product_id,
            'buyerEmail': buyer_email,
            'sellerEmail': seller_email,
            'title': sample.get('title', 'สินค้า'),
            'price': sample.get('price', ''),
            'status': 'รอชำระ',
            'createdAt': utc_now(),
        }
        table.put_item(Item=order)
        return response(200, {'message': 'สั่งซื้อสำเร็จ รอผู้ขายยืนยัน', 'order': order})
    except cognito.exceptions.NotAuthorizedException:
        return response(401, {'error': 'Token หมดอายุ'})
    except Exception as e:
        return response(500, {'error': str(e)})


def query_orders_index(index_name, key_name, email):
    from boto3.dynamodb.conditions import Key as DdbKey

    table = orders_table()
    if not table:
        return []
    result = table.query(
        IndexName=index_name,
        KeyConditionExpression=DdbKey(key_name).eq(email),
        ScanIndexForward=False,
    )
    return result.get('Items', [])


def handle_orders_buying(event):
    try:
        access_token = get_access_token(event)
        email, _ = get_cognito_user(access_token)
        orders = query_orders_index('buyer-email-index', 'buyerEmail', email)
        return response(200, {'orders': orders})
    except cognito.exceptions.NotAuthorizedException:
        return response(401, {'error': 'Token หมดอายุ'})
    except Exception as e:
        return response(500, {'error': str(e)})


def handle_orders_selling(event):
    try:
        access_token = get_access_token(event)
        email, _ = get_cognito_user(access_token)
        orders = query_orders_index('seller-email-index', 'sellerEmail', email)
        return response(200, {'orders': orders})
    except cognito.exceptions.NotAuthorizedException:
        return response(401, {'error': 'Token หมดอายุ'})
    except Exception as e:
        return response(500, {'error': str(e)})


def response(status_code, body):
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
        },
        'body': json.dumps(body, ensure_ascii=False),
    }
