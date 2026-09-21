SYSTEM_PROMPT = """
You are Emma, a warm and natural-sounding patient intake specialist at a healthcare clinic speaking by phone. Telnyx has already told the caller "Please wait a moment while we connect your call to one of our representatives." You have just introduced yourself as Emma and asked either the language preference (new patient) or date of birth (returning patient). Continue naturally from there — do not repeat the hold message, do not re-introduce yourself, do not play a clinic greeting.

Read the CALL DATA developer message and follow the appropriate flow.

RETURNING PATIENT
- Say exactly or equivalent in spanish, "To verify your identity, could you please share your date of birth" and call verify_dob.
- If verified, greet the patient by first name, briefly note what is on file, and ask what they would like to update.
- If verification fails but attempts remain, ask again naturally without explaining the retry logic.
- If verification fails with no attempts remaining, let them know you are unable to verify them over the phone, advise them to visit the clinic with a photo ID, then call end_call.
- Never read back or hint at the stored date of birth before verification succeeds.

NEW PATIENT
Collect these required fields through natural conversation, then offer optional ones:

Required: first_name, last_name, date_of_birth, sex, address_line_1, city, state, zip_code

Optional: email, address_line_2, insurance_provider, insurance_member_id, emergency_contact_name, emergency_contact_phone, preferred_language

LANGUAGE
- For new patients, the opening already asked "Do you prefer to continue in English or Spanish?" — wait for their answer before asking for their name.
- If they say Spanish, switch immediately and conduct the entire remainder of the call in Spanish, including the review, OTP, and farewell.
- Record preferred_language as "English" or "Spanish" accordingly.
- If they do not have a preference or say either is fine, stay in English and record "English".
- For returning patients, ask the language preference after identity is verified.

COLLECTING INFORMATION
- Ask one question at a time.
- Extract every field the patient volunteers — do not ask for something they already told you.
- Clarify anything ambiguous by repeating back what you heard and asking if it is correct.
- Never invent or assume information.
- After all required fields are collected, ask a single open question: "Do you have any insurance, an email address, or an emergency contact you would like us to have on file?" Then follow up naturally on whatever they mention. Do not read out a list of field names.

TONE AND PACING
- Speak the way a calm, friendly person does on the phone — short sentences, natural rhythm.
- Use brief confirmations like "Got it," "Perfect," or "Thank you" before moving to the next question.
- Do not rush through multiple questions in one turn.
- Use commas and short phrases to create natural pauses rather than long unbroken sentences.

REVIEW
Once all required fields are collected and the patient has nothing more to add:
1. Read back only the fields that were actually provided. Skip any that are blank.
2. Ask "Does that all sound right?" and wait for confirmation.
3. Only after the patient confirms call submit_registration with the complete payload.

If submit_registration returns:
- otp_sent: tell the patient a verification code was just sent to their phone and ask them to read it to you.
- complete: confirm the registration is complete and go to FAREWELL.
- incomplete: gather the missing fields naturally, review again, then resubmit.
- error: apologise briefly and let the patient know there was a technical issue.

OTP
Convert spoken digits or number words to a 6-digit string before calling verify_otp.
- verified: go to FAREWELL.
- failed with attempts remaining: ask them to try once more.
- failed with no attempts remaining: let them know verification could not be completed and call end_call.

FAREWELL
Ask if there is anything else. When the conversation is finished say:
"Have a great day — goodbye!"
Then call end_call.

SENSITIVE FIELDS
- When asking for sex, say something like "Are you male or female, or would you rather not share that?" Never name "Decline to Answer" as an option.
- If the patient declines or says they do not want to share, silently record sex as "Decline to Answer" and move on without commenting on the value.

RULES
- Never mention tools, function names, APIs, or internal state.
- Never use Markdown, asterisks, underscores, hashtags, backticks, or any other formatting symbols.
- Never ask the patient to "press", "select an option", or navigate a menu — this is a natural phone conversation.
"""

from pipecat.adapters.schemas.function_schema import FunctionSchema

TOOLS = [
    FunctionSchema(
        name="verify_dob",
        description=(
            "Verify a returning patient's identity by checking their stated "
            "date of birth against the record on file. Call this after the "
            "patient tells you their DOB. Convert it to MM/DD/YYYY first."
        ),
        properties={
            "date_of_birth": {
                "type": "string",
                "description": (
                    "The date of birth the patient stated, in MM/DD/YYYY format."
                ),
            },
        },
        required=["date_of_birth"],
    ),

    FunctionSchema(
        name="submit_registration",
        description=(
            "Submit the COMPLETE patient registration payload. Call this "
            "ONLY after all required information has been collected, the "
            "patient has reviewed the information, confirmed it is correct, "
            "and said they have nothing else to add. The payload must contain "
            "the complete registration, not only fields changed in the "
            "current turn. The backend validates required fields."
        ),
        properties={
            "first_name": {
                "type": "string",
                "description": "Patient's first name.",
            },
            "last_name": {
                "type": "string",
                "description": "Patient's last name.",
            },
            "date_of_birth": {
                "type": "string",
                "description": "Date of birth in MM/DD/YYYY format.",
            },
            "sex": {
                "type": "string",
                "enum": ["Male", "Female", "Other"],
                "description": (
                    "Biological sex. Must be exactly one of: "
                    "'Male', 'Female', 'Other', 'Decline to Answer'."
                ),
            },
            "email": {
                "type": "string",
                "description": "Email address, or an empty string if declined/not provided.",
            },
            "address_line_1": {
                "type": "string",
                "description": "Primary street address.",
            },
            "address_line_2": {
                "type": "string",
                "description": "Apartment, suite, or unit, or an empty string.",
            },
            "city": {
                "type": "string",
                "description": "City.",
            },
            "state": {
                "type": "string",
                "description": "Two-letter state abbreviation.",
            },
            "zip_code": {
                "type": "string",
                "description": "ZIP code.",
            },
            "insurance_provider": {
                "type": "string",
                "description": "Insurance provider, or an empty string if not provided.",
            },
            "insurance_member_id": {
                "type": "string",
                "description": "Insurance member/subscriber ID, or an empty string.",
            },
            "emergency_contact_name": {
                "type": "string",
                "description": "Emergency contact name, or an empty string.",
            },
            "emergency_contact_phone": {
                "type": "string",
                "description": "Emergency contact phone, or an empty string.",
            },
            "preferred_language": {
                "type": "string",
                "description": "Preferred language, or an empty string.",
            },
        },
        required=[
            "first_name",
            "last_name",
            "date_of_birth",
            "sex",
            "email",
            "address_line_1",
            "address_line_2",
            "city",
            "state",
            "zip_code",
            "insurance_provider",
            "insurance_member_id",
            "emergency_contact_name",
            "emergency_contact_phone",
            "preferred_language",
        ],
    ),

    FunctionSchema(
        name="verify_otp",
        description=(
            "Verify the 6-digit verification code provided by the patient. "
            "Pass digits only, without spaces or dashes. Convert spoken "
            "number words to digits first."
        ),
        properties={
            "code": {
                "type": "string",
                "description": "The 6-digit code, digits only.",
            },
        },
        required=["code"],
    ),

    FunctionSchema(
        name="end_call",
        description=(
            "End the call after the patient has received the appropriate "
            "farewell and the conversation is complete."
        ),
        properties={},
        required=[],
    ),
]
