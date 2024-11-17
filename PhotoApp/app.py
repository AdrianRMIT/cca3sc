from flask import Flask, render_template, request, redirect, url_for, session
import os
import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Attr
from boto3.dynamodb.conditions import Key
import uuid

app = Flask(__name__)
app.secret_key = "session_key"

# Set up DynamoDB
dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
login_table = dynamodb.Table('login')
photos_table = dynamodb.Table('photos')  

# Initialize the S3 client
s3 = boto3.client('s3', region_name='us-east-1')
S3_BUCKET_NAME = 'photo-app-uploads'

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        try:
            response = login_table.get_item(Key={'email': email})
            user = response.get('Item')
            if user and user['password'] == password:
                session['user_email'] = user['email']
                session['user_name'] = user['user_name']
                return redirect(url_for('main'))
            else:
                error = "Email or password is invalid"
        except Exception as e:
            error = f"An error occurred while logging in: {str(e)}"
    return render_template('login.html', error=error)

@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None
    if request.method == 'POST':
        email = request.form['email']
        user_name = request.form['username']
        password = request.form['password']
        try:
            response = login_table.get_item(Key={'email': email})
            if 'Item' in response:
                error = "The email already exists"
            else:
                login_table.put_item(Item={
                    'email': email,
                    'user_name': user_name,
                    'password': password
                })
                return redirect(url_for('login'))
        except Exception as e:
            error = f"An error occurred during registration: {str(e)}"
    return render_template('register.html', error=error)

@app.route('/logout')
def logout():
    session.pop('user_name', None)
    session.pop('user_email', None)
    return redirect(url_for('login'))

@app.route('/main')
def main():
    if 'user_email' in session:
        return render_template('main.html', user_name=session['user_name'])
    return redirect(url_for('login'))

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/upload_image', methods=['POST'])
def upload_image():
    if 'user_email' not in session:
        return redirect(url_for('login'))

    # Get form data
    camera = request.form['camera']
    location = request.form['location']
    focal_length = request.form['focal_length']  
    file = request.files['file']

    if file and file.filename != '':
        try:
            # Generate unique S3 key and image_id
            user_email = session['user_email']
            image_id = str(uuid.uuid4())  # Unique image ID for each upload
            s3_key = f"{user_email}/{image_id}.jpg"

            # Upload the file to S3
            s3.upload_fileobj(file, S3_BUCKET_NAME, s3_key, ExtraArgs={"ContentType": file.content_type})
            s3_url = f"https://{S3_BUCKET_NAME}.s3.amazonaws.com/{s3_key}"

            # Store metadata in DynamoDB
            photos_table.put_item(
                Item={
                    'user_email': user_email,
                    'image_id': image_id,
                    'camera': camera,
                    'location': location,
                    'focal_length_35mm_equiv': int(focal_length),
                    's3_url': s3_url
                }
            )
            return render_template('main.html', user_name=session['user_name'], message="Image uploaded successfully.")

        except Exception as e:
            print(f"An error occurred: {e}")
            return render_template('main.html', user_name=session['user_name'], message="Failed to upload image.")
    else:
        return redirect(url_for('main'))

@app.route('/query_images', methods=['POST'])
def query_images():
    if 'user_email' not in session:
        return redirect(url_for('login'))

    location = request.form.get('location', '').strip()
    camera = request.form.get('camera', '').strip()
    focal_length = request.form.get('focal_length', '').strip()
    
    query_conditions = []
    expression_values = {}

    search_performed = True

    if location:
        query_conditions.append(Attr('location').eq(location))
    if camera:
        query_conditions.append(Attr('camera').eq(camera))
    if focal_length:
        try:
            query_conditions.append(Attr('focal_length_35mm_equiv').eq(int(focal_length)))
        except ValueError:
            pass  

    try:
        if query_conditions:
            filter_expression = query_conditions[0]
            for condition in query_conditions[1:]:
                filter_expression = filter_expression & condition

            response = photos_table.scan(FilterExpression=filter_expression)
            query_results = response.get('Items', [])
            query_error = "No results found." if not query_results else None
        else:
            query_results = []
            query_error = "Please enter search criteria."

        return render_template('main.html', user_name=session['user_name'],
                               query_results=query_results, query_error=query_error, search_performed=search_performed)

    except Exception as e:
        query_error = f"An error occurred while querying images: {str(e)}"
        return render_template('main.html', user_name=session['user_name'], query_error=query_error, search_performed=search_performed)

@app.route('/profile/<user_email>')
def user_profile(user_email):
    if 'user_email' not in session:
        return redirect(url_for('login'))

    is_owner = session['user_email'] == user_email

    try:
        response = photos_table.query(
            KeyConditionExpression=Key('user_email').eq(user_email)
        )
        photos = response.get('Items', [])
        print(f"Photos for {user_email}: {photos}")  
    except Exception as e:
        print(f"Error fetching photos for user {user_email}: {e}")
        photos = []

    return render_template('profile.html', user_email=user_email, photos=photos, is_owner=is_owner)

@app.route('/delete_photo/<user_email>/<image_id>', methods=['POST'])
def delete_photo(user_email, image_id):
    if 'user_email' not in session or session['user_email'] != user_email:
        return redirect(url_for('login'))
    try:
        photos_table.delete_item(
            Key={
                'user_email': user_email,
                'image_id': image_id
            }
        )
        return redirect(url_for('user_profile', user_email=user_email))
    except Exception as e:
        print(f"Error deleting photo {image_id} for user {user_email}: {e}")
        return redirect(url_for('user_profile', user_email=user_email))

if __name__ == "__main__":
    app.run(debug=True)
