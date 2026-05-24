# prob not a functional script. just a record of everything i ran
git submodules init --recursive
python3 -m venv .venv
source .venv/bin/activate
cd cinderx && pip install -e . && cd ..

