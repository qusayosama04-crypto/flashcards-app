import os
import random
from dotenv import load_dotenv
from PIL import Image

load_dotenv()
from flask import Flask, render_template, request, redirect, flash, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import fitz
import json
from google import genai

app = Flask(__name__)

# إعدادات الحماية وقاعدة البيانات
app.config['SECRET_KEY'] = 'my_super_secret_key_for_brand'
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///flashcards.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 👇 الحل السحري لمنع انقطاع قاعدة البيانات 👇
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True}

db = SQLAlchemy(app)

# إعداد الذكاء الاصطناعي
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# إعداد نظام تسجيل الدخول
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# --- نماذج قاعدة البيانات ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(500), unique=True, nullable=False)
    password = db.Column(db.String(500), nullable=False)
    cards = db.relationship('Flashcard', backref='author', lazy=True)

class Flashcard(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False)
    front = db.Column(db.String(500), nullable=False)
    back = db.Column(db.String(2000), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

with app.app_context():
    db.create_all()

# --- مسارات الحسابات ---
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user:
            flash('اسم المستخدم موجود مسبقاً، اختر اسماً آخر.')
            return redirect(url_for('signup'))
        hashed_password = generate_password_hash(password)
        new_user = User(username=username, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        login_user(new_user)
        return redirect(url_for('index'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user, remember=True)
            return redirect(url_for('index'))
        else:
            flash('اسم المستخدم أو كلمة المرور غير صحيحة.')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- مسارات التطبيق الرئيسية ---
@app.route('/')
def index():
    if not current_user.is_authenticated:
        return render_template('index.html', cards=[])
    search_query = request.args.get('search')
    if search_query:
        all_cards = Flashcard.query.filter(
            Flashcard.user_id == current_user.id,
            ((Flashcard.front.contains(search_query)) | (Flashcard.category.contains(search_query)))
        ).all()
    else:
        all_cards = Flashcard.query.filter_by(user_id=current_user.id).all()
    return render_template('index.html', cards=all_cards)

@app.route('/add', methods=['POST'])
@login_required
def add_card():
    card_category = request.form.get('category') or "عام"
    card_front = request.form.get('front')
    card_back = request.form.get('back')
    if card_front and card_back:
        new_card = Flashcard(category=card_category, front=card_front, back=card_back, user_id=current_user.id)
        db.session.add(new_card)
        db.session.commit()
    return redirect('/')

@app.route('/delete/<int:id>')
@login_required
def delete_card(id):
    card_to_delete = db.session.get(Flashcard, id)
    if card_to_delete and card_to_delete.user_id == current_user.id:
        db.session.delete(card_to_delete)
        db.session.commit()
    return redirect('/')

# --- الميزة القديمة: تحويل PDF ---
@app.route('/upload_pdf', methods=['POST'])
@login_required
def upload_pdf():
    pdf_category = request.form.get('pdf_category') or "ملف PDF"
    pdf_file = request.files.get('pdf_file')
    lang = request.form.get('language', 'ar')
    output_type = request.form.get('output_type', 'flashcards')

    if pdf_file and pdf_file.filename.endswith('.pdf'):
        try:
            pdf_document = fitz.open(stream=pdf_file.read(), filetype="pdf")
            text = "".join([page.get_text() for page in pdf_document])[:10000]
            pdf_document.close()

            if output_type == 'essay':
                prompt = f"""أنت خبير تعليمي. استخرج 5 أسئلة مقالية وإجاباتها النموذجية من النص. الناتج JSON فقط مصفوفة كائنات بمفتاحين: "front" و "back".\nالنص:\n{text}"""
            else:
                prompt = f"""أنت خبير تعليمي. استخرج أهم المعلومات كبطاقات سؤال وجواب قصيرة. الناتج JSON فقط مصفوفة كائنات بمفتاحين: "front" و "back".\nالنص:\n{text}"""

            response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            flashcards_data = json.loads(response.text.replace("```json", "").replace("```", "").strip())

            for card in flashcards_data:
                new_card = Flashcard(category=pdf_category, front=str(card.get('front'))[:499], back=str(card.get('back'))[:1999], user_id=current_user.id)
                db.session.add(new_card)
            db.session.commit()
        except Exception as e:
            pass
    return redirect('/')

# --- الميزة الجديدة 1: عين الذكاء الاصطناعي (تحويل الصور) 📸 ---
@app.route('/upload_image', methods=['POST'])
@login_required
def upload_image():
    img_category = request.form.get('img_category') or "صورة ذكية"
    image_file = request.files.get('image_file')
    
    if image_file:
        try:
            img = Image.open(image_file)
            prompt = "أنت خبير تعليمي. اقرأ المكتوب في هذه الصورة بدقة، واستخرج أهم المعلومات كبطاقات (سؤال وجواب) للمراجعة. الناتج يجب أن يكون JSON فقط كمصفوفة كائنات، كل كائن يحتوي على مفتاح 'front' للسؤال ومفتاح 'back' للإجابة."
            
            response = client.models.generate_content(model='gemini-2.5-flash', contents=[img, prompt])
            flashcards_data = json.loads(response.text.replace("```json", "").replace("```", "").strip())

            for card in flashcards_data:
                new_card = Flashcard(category=img_category, front=str(card.get('front'))[:499], back=str(card.get('back'))[:1999], user_id=current_user.id)
                db.session.add(new_card)
            db.session.commit()
        except Exception as e:
            pass
    return redirect('/')

# --- الميزة الجديدة 2: رابط المشاركة الفيروسي 🔗 ---
@app.route('/share/<int:share_user_id>')
def share_deck(share_user_id):
    if not current_user.is_authenticated:
        flash("عليك تسجيل الدخول أولاً لنسخ بطاقات صديقك! 🚀")
        return redirect(url_for('login'))
        
    if current_user.id == share_user_id:
        flash("هذه هي بطاقاتك بالفعل يا بطل! 😂")
        return redirect('/')
        
    shared_cards = Flashcard.query.filter_by(user_id=share_user_id).all()
    if not shared_cards:
        flash("لا توجد بطاقات لدى هذا المستخدم.")
        return redirect('/')
        
    for card in shared_cards:
        new_card = Flashcard(category=card.category + " (من صديق)", front=card.front, back=card.back, user_id=current_user.id)
        db.session.add(new_card)
    db.session.commit()
    return redirect('/')

@app.route('/api/simplify', methods=['POST'])
@login_required
def simplify_answer():
    data = request.get_json()
    try:
        prompt = f"اشرح هذه المعلومة بأسلوب مبسط جداً وكأنك تشرحها لطفل في العاشرة. اجعل الشرح قصيراً:\n\n{data.get('text')}"
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        return jsonify({'simplified_text': response.text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/ai_quiz_generate')
@login_required
def ai_quiz_generate():
    num_q = request.args.get('num_q', 5, type=int) 
    all_user_cards = Flashcard.query.filter_by(user_id=current_user.id).all()
    
    if len(all_user_cards) < 3:
        return redirect('/')
    
    sample_size = min(len(all_user_cards), max(num_q * 2, 10))
    selected_cards = random.sample(all_user_cards, sample_size)
    cards_text = [{"q": c.front, "a": c.back} for c in selected_cards]
    
    prompt = f"أنت خبير تعليمي. أنشئ اختبار MCQ من {num_q} أسئلة بناءً على البطاقات. الناتج JSON مصفوفة كائنات (question, options, correct_index).\nالبطاقات:\n{json.dumps(cards_text, ensure_ascii=False)}"
    
    try:
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        quiz_data = json.loads(response.text.replace("```json", "").replace("```", "").strip())
        return render_template('ai_quiz.html', quiz=quiz_data)
    except Exception as e:
        return redirect('/')

@app.route('/quiz')
@login_required
def quiz():
    user_cards = Flashcard.query.filter_by(user_id=current_user.id).all()
    cards_data = [{'front': card.front, 'back': card.back, 'category': card.category} for card in user_cards]
    return render_template('quiz.html', cards=cards_data)

if __name__ == '__main__':
    app.run(debug=True)
