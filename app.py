import os
from dotenv import load_dotenv

load_dotenv() # هذا السطر يقرأ ملف الأسرار المخفي
from flask import Flask, render_template, request, redirect, flash, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import fitz
import json
from google import genai

app = Flask(__name__)

# إعدادات الحماية وقاعدة البيانات
app.config['SECRET_KEY'] = 'my_super_secret_key_for_brand' # مفتاح سري لحماية جلسات المستخدمين
# جلب رابط قاعدة البيانات السحابية، وإذا لم يوجد نستخدم الملف المحلي
db_url = os.environ.get("DATABASE_URL")

# إصلاح مشكلة شائعة في بعض الروابط
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///flashcards.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# إعداد الذكاء الاصطناعي (تذكر وضع مفتاحك هنا)
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# إعداد نظام تسجيل الدخول
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # توجيه المستخدم غير المسجل لصفحة تسجيل الدخول

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- نماذج قاعدة البيانات ---
# 1. جدول المستخدمين
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(500), unique=True, nullable=False)
    password = db.Column(db.String(500), nullable=False)
    # علاقة: المستخدم يمتلك عدة بطاقات
    cards = db.relationship('Flashcard', backref='author', lazy=True)

# 2. جدول البطاقات (تم تحديثه ليرتبط بالمستخدم)
class Flashcard(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False)
    front = db.Column(db.String(500), nullable=False)
    back = db.Column(db.String(1000), nullable=False)
    # عمود جديد: رقم المستخدم صاحب البطاقة
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

with app.app_context():
    db.create_all()

# --- مسارات الحسابات (تسجيل الدخول وإنشاء حساب) ---

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # التأكد من أن اسم المستخدم غير موجود مسبقاً
        user = User.query.filter_by(username=username).first()
        if user:
            flash('اسم المستخدم موجود مسبقاً، اختر اسماً آخر.')
            return redirect(url_for('signup'))
        
        # تشفير كلمة المرور لحمايتها وحفظ المستخدم الجديد
        hashed_password = generate_password_hash(password)
        new_user = User(username=username, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        
        # تسجيل الدخول تلقائياً بعد إنشاء الحساب
        login_user(new_user)
        return redirect(url_for('index'))
        
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # البحث عن المستخدم
        user = User.query.filter_by(username=username).first()
        # التأكد من المستخدم وكلمة المرور
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

# --- مسارات البطاقات التعليمية ---

@app.route('/')
def index():
    # 1. إذا كان المستخدم ضيفاً (غير مسجل الدخول)، اعرض له واجهة فارغة ليجربها
    if not current_user.is_authenticated:
        return render_template('index.html', cards=[])
        
    # 2. إذا كان المستخدم مسجل الدخول، نفذ كودك الأصلي للبحث وعرض البطاقات
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
        # ربط البطاقة بالمستخدم الحالي (user_id=current_user.id)
        new_card = Flashcard(category=card_category, front=card_front, back=card_back, user_id=current_user.id)
        db.session.add(new_card)
        db.session.commit()
    return redirect('/')

@app.route('/delete/<int:id>')
@login_required
def delete_card(id):
    card_to_delete = Flashcard.query.get_or_404(id)
    # التأكد من أن البطاقة تخص المستخدم الحالي قبل حذفها
    if card_to_delete.user_id == current_user.id:
        db.session.delete(card_to_delete)
        db.session.commit()
    return redirect('/')

@app.route('/upload_pdf', methods=['POST'])
@login_required
def upload_pdf():
    pdf_category = request.form.get('pdf_category') or "ملف PDF"
    pdf_file = request.files.get('pdf_file')

    if pdf_file and pdf_file.filename.endswith('.pdf'):
        try:
            pdf_document = fitz.open(stream=pdf_file.read(), filetype="pdf")
            text = ""
            for page in pdf_document:
                text += page.get_text()
            pdf_document.close()

            text = text[:10000]

            prompt = f"""
            أنت خبير تعليمي. قم بقراءة النص التالي واستخرج منه أهم المعلومات في صيغة أسئلة وإجابات لعمل بطاقات تعليمية.
            يجب أن يكون الناتج بصيغة JSON فقط، عبارة عن مصفوفة (Array) تحتوي على كائنات، كل كائن له مفتاحين:
            "front": يمثل السؤال.
            "back": يمثل الإجابة.
            النص:
            {text}
            """

            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
            ai_text = response.text.replace("```json", "").replace("```", "").strip()
            flashcards_data = json.loads(ai_text)

            for card in flashcards_data:
                # ربط البطاقات الذكية بالمستخدم الحالي
                new_card = Flashcard(
                    category=pdf_category, 
                    front=str(card.get('front', 'سؤال'))[:499], 
                    back=str(card.get('back', 'إجابة'))[:999],
                    user_id=current_user.id 
                )
                db.session.add(new_card)
            
            db.session.commit()
        except Exception as e:
            print(f"\n--- حدث خطأ --- \n{e}\n----------------\n")

    return redirect('/')
# --- مسار وضع الاختبار (Quiz Mode) ---
@app.route('/quiz')
@login_required
def quiz():
    # جلب جميع بطاقات المستخدم الحالي
    user_cards = Flashcard.query.filter_by(user_id=current_user.id).all()
    
    # إذا لم يكن لديه بطاقات، نعيده للصفحة الرئيسية مع رسالة
    if not user_cards:
        flash('ليس لديك أي بطاقات لمراجعتها. قم بإضافة بعض البطاقات أولاً! 📚')
        return redirect(url_for('index'))
        
    # تحويل البطاقات إلى قائمة قواميس (Dictionaries) ليسهل إرسالها لـ JavaScript
    cards_data = []
    for card in user_cards:
        cards_data.append({
            'front': card.front,
            'back': card.back,
            'category': card.category
        })
        
    return render_template('quiz.html', cards=cards_data)
if __name__ == '__main__':
    app.run(debug=True)
