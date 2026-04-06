import os
import random
from dotenv import load_dotenv

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
            text = ""
            for page in pdf_document:
                text += page.get_text()
            pdf_document.close()
            text = text[:10000]

            if output_type == 'essay':
                if lang == 'en':
                    prompt = f"""You are an expert tutor. Extract 5 comprehensive essay questions and their detailed model answers from the text. Output STRICT JSON as an array of objects with keys: "front" (the question) and "back" (the model answer).\nText:\n{text}"""
                else:
                    prompt = f"""أنت خبير تعليمي. استخرج 5 أسئلة مقالية (طويلة) وإجاباتها النموذجية الشاملة من هذا النص. الناتج يجب أن يكون JSON فقط مصفوفة كائنات بمفتاحين: "front" للسؤال و "back" للإجابة.\nالنص:\n{text}"""
            else:
                if lang == 'en':
                    prompt = f"""You are an educational expert. Extract the most important facts into short Q&A flashcards. Output STRICT JSON as an array of objects with keys: "front" (Question) and "back" (Answer).\nText:\n{text}"""
                else:
                    prompt = f"""أنت خبير تعليمي. استخرج أهم المعلومات كبطاقات سؤال وجواب قصيرة. الناتج يجب أن يكون JSON فقط مصفوفة كائنات بمفتاحين: "front" للسؤال و "back" للإجابة.\nالنص:\n{text}"""

            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
            ai_text = response.text.replace("```json", "").replace("```", "").strip()
            flashcards_data = json.loads(ai_text)

            for card in flashcards_data:
                new_card = Flashcard(
                    category=pdf_category, 
                    front=str(card.get('front', 'سؤال'))[:499], 
                    back=str(card.get('back', 'إجابة'))[:1999],
                    user_id=current_user.id 
                )
                db.session.add(new_card)
            db.session.commit()
        except Exception as e:
            flash("حدث خطأ أثناء معالجة الملف.")
    return redirect('/')

@app.route('/api/simplify', methods=['POST'])
@login_required
def simplify_answer():
    data = request.get_json()
    text_to_simplify = data.get('text')
    lang = data.get('lang', 'ar')

    if not text_to_simplify:
        return jsonify({'error': 'No text provided'}), 400

    try:
        if lang == 'en':
            prompt = f"Explain the following concept in very simple terms, like you are explaining it to a 10-year-old child. Keep it short, fun, and easy to understand:\n\n{text_to_simplify}"
        else:
            prompt = f"اشرح هذه المعلومة بأسلوب مبسط جداً وكأنك تشرحها لطفل في العاشرة من عمره. استخدم تشبيهات ممتعة واجعل الشرح قصيراً وسهل الحفظ:\n\n{text_to_simplify}"

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        return jsonify({'simplified_text': response.text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- الميزة الجديدة والمحسنة: تحدي الذكاء الاصطناعي مع اختيار العدد ---
@app.route('/ai_quiz_generate')
@login_required
def ai_quiz_generate():
    # استقبال عدد الأسئلة من الواجهة (وإذا لم يختر، يكون 5 افتراضياً)
    num_q = request.args.get('num_q', 5, type=int) 
    
    # جلب كل بطاقات المستخدم
    all_user_cards = Flashcard.query.filter_by(user_id=current_user.id).all()
    
    if len(all_user_cards) < 3:
        flash("تحتاج إلى إضافة 3 بطاقات على الأقل لبدء تحدي الذكاء الاصطناعي! 📚")
        return redirect('/')
    
    # اختيار عينة عشوائية من البطاقات لتنويع الأسئلة في كل مرة (نرسل للذكاء الاصطناعي بطاقات أكثر قليلاً ليختار منها)
    sample_size = min(len(all_user_cards), max(num_q * 2, 10))
    selected_cards = random.sample(all_user_cards, sample_size)
    
    cards_text = [{"q": c.front, "a": c.back} for c in selected_cards]
    
    prompt = f"أنت خبير تعليمي. بناءً على هذه البطاقات، أنشئ اختبار اختيار من متعدد (MCQ) تفاعلي يتكون من {num_q} أسئلة بالضبط. الناتج يجب أن يكون JSON فقط كمصفوفة كائنات، كل كائن يحتوي على: 'question' (السؤال)، 'options' (مصفوفة من 4 خيارات)، و 'correct_index' (رقم الخيار الصحيح من 0 إلى 3).\nالبطاقات:\n{json.dumps(cards_text, ensure_ascii=False)}"
    
    try:
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        ai_text = response.text.replace("```json", "").replace("```", "").strip()
        quiz_data = json.loads(ai_text)
        return render_template('ai_quiz.html', quiz=quiz_data)
    except Exception as e:
        flash("حدث خطأ أثناء توليد التحدي، جرب مرة أخرى.")
        return redirect('/')

@app.route('/quiz')
@login_required
def quiz():
    user_cards = Flashcard.query.filter_by(user_id=current_user.id).all()
    if not user_cards:
        flash('ليس لديك أي بطاقات لمراجعتها. قم بإضافة بعض البطاقات أولاً! 📚')
        return redirect(url_for('index'))
    cards_data = [{'front': card.front, 'back': card.back, 'category': card.category} for card in user_cards]
    return render_template('quiz.html', cards=cards_data)

if __name__ == '__main__':
    app.run(debug=True)
