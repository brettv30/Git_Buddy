import os
import logging
import pinecone
import langchain
from dotenv import load_dotenv
from langchain.prompts import PromptTemplate
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.vectorstores import Pinecone
from langchain.chat_models import ChatOpenAI
from langchain.chains.conversation.memory import ConversationBufferWindowMemory
from langchain.tools import DuckDuckGoSearchRun
from langchain.agents import Tool
from langchain.chains import LLMChain
from CustomPromptTemplate import *
from CustomOutputParser import *

# Initialize logging
logging.basicConfig(level=logging.INFO)

# Load Environment Variables
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")

# Set Const Variables
EMBEDDINGS_MODEL = "text-embedding-ada-002"
INDEX_NAME = "git-buddy-index"
MODEL_NAME = "gpt-3.5-turbo"
PROMPT_TEMPLATE = """You are Git Buddy, a helpful assistant that teaches Git, GitHub, and TortoiseGit to beginners. Your responses are geared towards beginners. 
You should only ever answer questions about Git, GitHub, or TortoiseGit. Never answer any other questions even if you think you know the correct answer. 
If possible, please provide example code and always provide the name of the file you pulled an answer from if you pulled an answer from the context.

Use the following pieces of context to answer the question at the end: 
{context}

Question: {input}
Answer:"""

# Validate environment variables
if not OPENAI_API_KEY or not PINECONE_API_KEY:
    logging.error(
        "Missing required environment variables. Please check your .env file."
    )
    exit(1)

# Initialize Pinecone
pinecone.init(api_key=PINECONE_API_KEY, environment="gcp-starter")
embeddings = OpenAIEmbeddings(model=EMBEDDINGS_MODEL)
index = Pinecone.from_existing_index(INDEX_NAME, embeddings)

search = DuckDuckGoSearchRun()


def duck_wrapper_github(input_text):
    search_results = search.run(f"site:https://docs.github.com/en {input_text}")
    return search_results


def duck_wrapper_git(input_text):
    search_results = search.run(f"site:https://git-scm.com {input_text}")
    return search_results


def get_similar_docs(query: str, k: int = 3, score: bool = False) -> list:
    """Retrieve similar documents from the index based on the given query."""
    return (
        index.similarity_search_with_score(query, k=k)
        if score
        else index.similarity_search(query, k=k)
    )


def retreive_answer(query: str) -> str:
    """Generate an answer based on similar documents and the provided query."""
    similar_docs = get_similar_docs(query)
    answer = qa_llm.run(
        {
            "context": similar_docs,
            "input": query,
        }
    )
    return answer


github_docs_search_tool = Tool(
    name="Search GitHub",
    func=duck_wrapper_github,
    description="Useful for when you need to answer questions related to GitHub documentation",
)

git_docs_search_tool = Tool(
    name="Search Git",
    func=duck_wrapper_git,
    description="Useful for when you need to answer questions related to Git documentation",
)

retreival_tool = Tool(
    name="Document Retreiver",
    func=retreive_answer,
    description="Useful for when you need to look up documentation before answering a question related to Git, GitHub, or TortoiseGit",
)

tools = [git_docs_search_tool, github_docs_search_tool, retreival_tool]

# Set up the base template
template = """You are Git Buddy, a helpful assistant that teaches Git, GitHub, and TortoiseGit to beginners. Your responses are geared towards beginners. 
You should only ever answer questions about Git, GitHub, or TortoiseGit. Never answer any other questions even if you think you know the correct answer. You have access to the following tools: 

{tools}

Always use the Document Retreiver tool first before attempting to use other tools.
Use the following format:

Question: the input question you must answer
Thought: you should always think about what to do
Action: the action to take, should be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)
Thought: I now know the final answer
Final Answer: the final answer to the original input question

Begin! Remember to answer as a helpful assistant when giving your final answer. 

Previous conversation history:
{history}

Question: {input}
{agent_scratchpad}"""

prompt = CustomPromptTemplate(
    template=template,
    tools=tools,
    # This omits the `agent_scratchpad`, `tools`, and `tool_names` variables because those are generated dynamically
    # This includes the `intermediate_steps` variable because that is needed
    input_variables=["input", "intermediate_steps", "history"],
)

output_parser = CustomOutputParser()

# Set up the LLM
llm = ChatOpenAI(temperature=0.5, model_name=MODEL_NAME)

# LLM chain consisting of the LLM and a prompt
llm_chain = LLMChain(llm=llm, prompt=prompt)

# Set up additional Retreival LLM tool
retreival_prompt = PromptTemplate(
    input_variables=["context", "input"], template=PROMPT_TEMPLATE
)
qa_llm = LLMChain(llm=llm, prompt=retreival_prompt, verbose=True)


tool_names = [tool.name for tool in tools]

agent = LLMSingleActionAgent(
    llm_chain=llm_chain,
    output_parser=output_parser,
    stop=["\nObservation:"],
    allowed_tools=tool_names,
)

# conversational agent memory
memory = ConversationBufferWindowMemory(k=3)

agent_executor = AgentExecutor.from_agent_and_tools(
    agent=agent, tools=tools, verbose=True, memory=memory
)

query = "What is the difference between Git and GitHub?"
langchain.debug = True
try:
    response = agent_executor.run(input=query)
    print(response)
except ValueError as e:
    response = str(e)
    if not response.startswith("Could not parse LLM output: `"):
        raise e
    response = response.removeprefix("Could not parse LLM output: `").removesuffix("`")
    print(response)
langchain.debug = False
